"""
ARQ jobs for the GDPR module.

purge_user_data (T-036): hard-deletes a soft-deleted user's data once T-033's
GDPR_PURGE_GRACE_DAYS grace period has elapsed (GDPRService.delete_me sets
User.deleted_at, then enqueues this job). Deletes go through plain
SQLAlchemy `delete()`/`update()` statements rather than each module's
repository, on purpose: AuditEvent and ConsentRecord are append-only
everywhere else (see app.modules.audit.repository / app.modules.gdpr.repository
-- neither exposes an update or delete method) per §0.3's "immutability where
it matters". The GDPR right to erasure is the one deliberate exception that
carves out, so this job bypasses the repositories instead of weakening them
for every other caller.

export_user_data (T-037): the right-to-access counterpart -- collects every
table GDPRService.request_export's caller is entitled to see, writes it as
data.json inside a zip, uploads it to the configured object store, signs a
24h download URL, and emails it via send_email. Talks to boto3 directly
rather than through a storage abstraction: the StorageBackend protocol that
unifies S3/R2/MinIO behind one interface doesn't land until T-111, and one
job's worth of put_object/generate_presigned_url calls doesn't justify
building that abstraction early (the same "no abstraction ahead of need"
call app.workers.jobs.email makes for Resend).

Both jobs skip Notification and StoredFile rows -- those models don't exist
until T-101 and T-110. When they land, add purge's entry to _DELETE_BY_USER
(StoredFile also needs a storage.delete(key) call per row, per the agents.md
T-036 spec, before the row itself is deleted) and export's entry to
_collect_export_payload.
"""

import asyncio
import json
import logging
import zipfile
from datetime import timedelta
from io import BytesIO
from typing import Any, cast
from uuid import UUID

import boto3
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import SQLModel

from app.core.config import get_settings
from app.core.db import async_session_factory
from app.core.tracing import trace_job
from app.models._mixins import utcnow
from app.modules.audit.models import AuditEvent
from app.modules.auth.models import (
    EmailVerificationToken,
    MagicLinkToken,
    OAuthAccount,
    PasswordResetToken,
    TOTPSecret,
    User,
)
from app.modules.auth.models import Session as SessionModel
from app.modules.auth.schemas import UserPublic
from app.modules.gdpr.models import ConsentRecord, DataExportJob
from app.modules.gdpr.repository import DataExportJobRepository
from app.modules.tenancy.models import Membership, Organization
from app.workers.jobs.email import send_email

logger = logging.getLogger(__name__)

_EXPORT_URL_TTL = timedelta(hours=24)
_EXPORT_URL_TTL_SECONDS = int(_EXPORT_URL_TTL.total_seconds())

# Dependency order per agents.md T-036 (minus Notification/StoredFile -- see
# module docstring). AuditEvent is filtered by actor_id, not user_id -- it's
# the one column here that isn't even a foreign key (see app.modules.audit.models).
_DELETE_BY_USER: tuple[tuple[type[SQLModel], str], ...] = (
    (DataExportJob, "user_id"),
    (ConsentRecord, "user_id"),
    (AuditEvent, "actor_id"),
    (MagicLinkToken, "user_id"),
    (PasswordResetToken, "user_id"),
    (EmailVerificationToken, "user_id"),
    (TOTPSecret, "user_id"),
    (OAuthAccount, "user_id"),
    (SessionModel, "user_id"),
    (Membership, "user_id"),
)


@trace_job
async def purge_user_data(
    ctx: dict[str, Any],  # noqa: ARG001 -- ARQ calling convention; registered in app.workers.main
    *,
    user_id: UUID,
) -> None:
    async with async_session_factory() as session:
        user = await session.get(User, user_id)
        if user is None or user.deleted_at is None:
            # Already purged, or delete_me's soft-delete was reversed during
            # the grace period -- either way there's nothing to do.
            logger.info("purge_user_data: user_id=%s is not pending deletion, skipping", user_id)
            return

        membership = cast("Any", Membership)
        org_ids_result = await session.execute(
            select(membership.org_id).where(membership.user_id == user_id)
        )
        org_ids: list[UUID] = list(org_ids_result.scalars().all())

        for model, column_name in _DELETE_BY_USER:
            column = getattr(cast("Any", model), column_name)
            await session.execute(delete(model).where(column == user_id))

        # Other users' Membership rows may record this user as their inviter
        # (Membership.invited_by, nullable) -- null those out so the FK to
        # user.id doesn't block the delete below. Invitation.invited_by is
        # NOT NULL, so a pending invitation this user sent is a known gap;
        # agents.md's T-036 purge order doesn't include Invitation.
        await session.execute(
            update(Membership).where(membership.invited_by == user_id).values(invited_by=None)
        )

        await session.execute(delete(User).where(cast("Any", User).id == user_id))
        await session.commit()

    await _detach_orphaned_org_billing(org_ids)
    logger.info("purge_user_data: purged all data for user_id=%s", user_id)


async def _detach_orphaned_org_billing(org_ids: list[UUID]) -> None:
    unique_org_ids = set(org_ids)
    if not unique_org_ids:
        return

    async with async_session_factory() as session:
        membership = cast("Any", Membership)
        organization = cast("Any", Organization)
        for org_id in unique_org_ids:
            remaining = (
                await session.execute(
                    select(func.count()).select_from(Membership).where(membership.org_id == org_id)
                )
            ).scalar_one()
            if remaining == 0:
                await session.execute(
                    update(Organization)
                    .where(organization.id == org_id)
                    .values(stripe_customer_id=None)
                )
        await session.commit()


# --- export_user_data (T-037) ------------------------------------------------


@trace_job
async def export_user_data(
    ctx: dict[str, Any],
    *,
    user_id: UUID,
    job_id: UUID,
) -> None:
    async with async_session_factory() as session:
        user = await session.get(User, user_id)
        if user is None:
            logger.info("export_user_data: user_id=%s no longer exists, skipping", user_id)
            return
        job = await DataExportJobRepository(session).get_by_id(job_id)
        if job is None or job.user_id != user_id:
            # Fail fast, before paying for the zip build + upload below.
            logger.warning(
                "export_user_data: job_id=%s missing or mismatched for user_id=%s, skipping",
                job_id,
                user_id,
            )
            return
        payload = await _collect_export_payload(session, user)

    zip_bytes = _build_export_zip(payload)
    download_url = await _upload_export_zip(user_id, job_id, zip_bytes)
    expires_at = utcnow() + _EXPORT_URL_TTL

    async with async_session_factory() as session:
        job = await DataExportJobRepository(session).get_by_id(job_id)
        if job is None:
            # Deleted between the check above and now -- the upload already
            # happened (cheap to leave it orphaned in the bucket vs. adding
            # transactional coupling here); just skip the status update + email.
            logger.warning("export_user_data: job_id=%s vanished mid-export, skipping", job_id)
            return
        job.status = "ready"
        job.download_url = download_url
        job.expires_at = expires_at
        await session.commit()

    await send_email(
        ctx,
        to=user.email,
        template="download_link",
        locale=user.locale,
        context={"download_url": download_url, "user_name": user.full_name},
    )
    logger.info("export_user_data: export ready for user_id=%s job_id=%s", user_id, job_id)


async def _collect_export_payload(session: AsyncSession, user: User) -> dict[str, Any]:
    """Everything GDPRService.get_export_job's caller is entitled to see about themselves."""
    session_model = cast("Any", SessionModel)
    oauth_account = cast("Any", OAuthAccount)
    consent_record = cast("Any", ConsentRecord)
    audit_event = cast("Any", AuditEvent)
    membership = cast("Any", Membership)

    session_rows = (
        (await session.execute(select(SessionModel).where(session_model.user_id == user.id)))
        .scalars()
        .all()
    )
    oauth_rows = (
        (await session.execute(select(OAuthAccount).where(oauth_account.user_id == user.id)))
        .scalars()
        .all()
    )
    consent_rows = (
        (await session.execute(select(ConsentRecord).where(consent_record.user_id == user.id)))
        .scalars()
        .all()
    )
    audit_rows = (
        (await session.execute(select(AuditEvent).where(audit_event.actor_id == user.id)))
        .scalars()
        .all()
    )
    membership_rows = (
        (await session.execute(select(Membership).where(membership.user_id == user.id)))
        .scalars()
        .all()
    )

    return {
        # UserPublic, not User.model_dump() -- excludes hashed_password,
        # failed_login_count, locked_until, same exclusions T-015 mandates
        # for anything that leaves the auth module.
        "user": UserPublic.model_validate(user).model_dump(mode="json"),
        "sessions": [row.model_dump(mode="json") for row in session_rows],
        "oauth_accounts": [row.model_dump(mode="json") for row in oauth_rows],
        "consent_records": [row.model_dump(mode="json") for row in consent_rows],
        "audit_events": [row.model_dump(mode="json") for row in audit_rows],
        "memberships": [row.model_dump(mode="json") for row in membership_rows],
    }


def _build_export_zip(payload: dict[str, Any]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("data.json", json.dumps(payload, indent=2))
    return buffer.getvalue()


def _s3_client() -> Any:  # noqa: ANN401 -- boto3 ships no stubs, see mypy.ini's [mypy-boto3.*]
    settings = get_settings()
    return boto3.client(
        "s3",
        region_name=settings.AWS_REGION,
        endpoint_url=settings.AWS_ENDPOINT_URL,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )


async def _upload_export_zip(user_id: UUID, job_id: UUID, zip_bytes: bytes) -> str:
    settings = get_settings()
    object_key = f"data-exports/{user_id}/{job_id}.zip"

    def _put_and_sign() -> str:
        client = _s3_client()
        client.put_object(
            Bucket=settings.AWS_BUCKET_NAME,
            Key=object_key,
            Body=zip_bytes,
            ContentType="application/zip",
        )
        url: str = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.AWS_BUCKET_NAME, "Key": object_key},
            ExpiresIn=_EXPORT_URL_TTL_SECONDS,
        )
        return url

    # boto3 is synchronous -- off-loaded to a thread so it doesn't block the
    # worker's event loop (and the other jobs it's running concurrently,
    # max_jobs=20 per app.workers.main.WorkerSettings) for the upload's duration.
    return await asyncio.to_thread(_put_and_sign)
