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

Purge covers every column that references a user through USER_REFERENCES;
tests/committed/test_gdpr_purge_job.py fails when a new foreign key to
user.id is not listed there.
"""

import asyncio
import json
import logging
import zipfile
from dataclasses import dataclass
from datetime import timedelta
from io import BytesIO
from typing import Any, Literal, cast
from uuid import UUID

import boto3
from redis.asyncio import Redis
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import SQLModel

from app.core.config import get_settings
from app.core.db import async_session_factory
from app.core.dependencies import invalidate_org_cache, invalidate_user_cache
from app.core.module_loader import is_enabled
from app.core.modules import ModuleName
from app.core.tracing import trace_job
from app.models._mixins import utcnow
from app.modules.api_keys.models import ApiKey
from app.modules.api_keys.schemas import ApiKeyPublic
from app.modules.audit.models import AuditEvent
from app.modules.auth.models import (
    EmailChangeToken,
    EmailVerificationToken,
    MagicLinkToken,
    OAuthAccount,
    PasswordResetToken,
    TOTPSecret,
    User,
    WebAuthnCredential,
)
from app.modules.auth.models import Session as SessionModel
from app.modules.auth.schemas import UserPublic
from app.modules.gdpr.models import ConsentRecord, DataExportJob
from app.modules.gdpr.repository import DataExportJobRepository
from app.modules.notifications.models import Notification, PushToken
from app.modules.storage.backend import get_storage_backend
from app.modules.storage.keys import delete_every_copy
from app.modules.storage.models import StoredFile
from app.modules.tenancy.models import Invitation, Membership, Organization
from app.modules.webhooks.models import WebhookEndpoint
from app.workers.jobs.email import send_email

logger = logging.getLogger(__name__)

_EXPORT_URL_TTL = timedelta(hours=24)
_EXPORT_URL_TTL_SECONDS = int(_EXPORT_URL_TTL.total_seconds())


@dataclass(frozen=True)
class UserReference:
    """A column that points at a user, and what purge does with it.

    `delete` removes the row -- it is the user's own data. `null` keeps the
    row and forgets who made it -- it belongs to the organization (an API key,
    a webhook endpoint, a colleague's membership). `module` names the module
    whose table it is; when that module is off the table does not exist.
    """

    model: type[SQLModel]
    column: str
    action: Literal["delete", "null"] = "delete"
    module: ModuleName | None = None


# In dependency order: children before the rows they point at.
USER_REFERENCES: tuple[UserReference, ...] = (
    UserReference(DataExportJob, "user_id"),
    UserReference(ConsentRecord, "user_id"),
    # Not a foreign key -- preserved after org deletion -- but it is the
    # user's activity trail, so it goes too.
    UserReference(AuditEvent, "actor_id"),
    UserReference(Notification, "user_id", module="notifications"),
    UserReference(PushToken, "user_id", module="notifications"),
    # T-036: the object in storage goes as well; see _delete_stored_objects.
    UserReference(StoredFile, "uploaded_by", module="file_storage"),
    UserReference(MagicLinkToken, "user_id"),
    UserReference(PasswordResetToken, "user_id"),
    UserReference(EmailVerificationToken, "user_id"),
    UserReference(EmailChangeToken, "user_id"),
    UserReference(TOTPSecret, "user_id"),
    UserReference(WebAuthnCredential, "user_id"),
    UserReference(OAuthAccount, "user_id"),
    UserReference(SessionModel, "user_id"),
    UserReference(Membership, "user_id"),
    # Invitations this user sent and nobody accepted. NOT NULL, so they can
    # only go; an accepted one already became a Membership.
    UserReference(Invitation, "invited_by"),
    UserReference(Membership, "invited_by", action="null"),
    # T-310: an API key this user minted belongs to their organization;
    # deleting it would take an integration down because a colleague
    # exercised a right that has nothing to do with it.
    UserReference(ApiKey, "created_by", action="null", module="public_api"),
    UserReference(WebhookEndpoint, "created_by", action="null", module="outbound_webhooks"),
)


@trace_job
async def purge_user_data(
    ctx: dict[str, Any],  # ARQ calling convention; registered in app.workers.main
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

        references = [r for r in USER_REFERENCES if r.module is None or is_enabled(r.module)]
        if is_enabled("file_storage"):
            await _delete_stored_objects(session, user_id)
        for reference in references:
            column = getattr(cast("Any", reference.model), reference.column)
            if reference.action == "delete":
                await session.execute(delete(reference.model).where(column == user_id))
            else:
                await session.execute(
                    update(reference.model)
                    .where(column == user_id)
                    .values({reference.column: None})
                )

        await session.execute(delete(User).where(cast("Any", User).id == user_id))
        await session.commit()

    # T-240: the row is gone from the DB but a copy of it may still be
    # readable from the user cache -- and unlike the soft-delete in
    # GDPRService.delete_me, there is no row left for a later read to
    # correct itself against.
    redis: Redis | None = ctx.get("redis")
    if redis is not None:
        await invalidate_user_cache(redis, user_id)

    await _detach_orphaned_org_billing(org_ids, redis)
    logger.info("purge_user_data: purged all data for user_id=%s", user_id)


async def _delete_stored_objects(session: AsyncSession, user_id: UUID) -> None:
    """The files this user uploaded, from storage, before their rows go.

    Storage first: a delete of an object that is already gone succeeds, so a
    retry after a failed commit is safe. The other order could commit the
    rows away and then fail, leaving the bytes with nothing pointing at them.
    """
    stored_file = cast("Any", StoredFile)
    keys = (
        await session.execute(select(stored_file.key).where(stored_file.uploaded_by == user_id))
    ).scalars()
    backend = get_storage_backend()
    for key in keys:
        await delete_every_copy(backend, key)


async def _detach_orphaned_org_billing(org_ids: list[UUID], redis: Redis | None) -> None:
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
                if redis is not None:
                    # An org write, so the cached Organization row behind
                    # get_current_org has to go with it (T-240).
                    await invalidate_org_cache(redis, org_id)
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


async def _rows(
    session: AsyncSession, model: type[SQLModel], column: str, user_id: UUID
) -> list[Any]:
    where = getattr(cast("Any", model), column) == user_id
    return list((await session.execute(select(model).where(where))).scalars().all())


def _dump(rows: list[Any], *, exclude: set[str] | None = None) -> list[dict[str, Any]]:
    return [row.model_dump(mode="json", exclude=exclude) for row in rows]


async def _collect_export_payload(session: AsyncSession, user: User) -> dict[str, Any]:
    """Everything GDPRService.get_export_job's caller is entitled to see about themselves.

    An export is a file a person downloads and forwards, so no secret-derived
    value goes in it: no refresh-token hash, no API key hash. Tables of
    optional modules are read only where the module is on -- selecting from a
    table that was never created is an error.
    """
    payload: dict[str, Any] = {
        # UserPublic, not User.model_dump() -- excludes hashed_password,
        # failed_login_count, locked_until, same exclusions T-015 mandates
        # for anything that leaves the auth module.
        "user": UserPublic.model_validate(user).model_dump(mode="json"),
        "sessions": _dump(
            await _rows(session, SessionModel, "user_id", user.id),
            exclude={"refresh_token_hash"},
        ),
        "oauth_accounts": _dump(await _rows(session, OAuthAccount, "user_id", user.id)),
        "consent_records": _dump(await _rows(session, ConsentRecord, "user_id", user.id)),
        "audit_events": _dump(await _rows(session, AuditEvent, "actor_id", user.id)),
        "memberships": _dump(await _rows(session, Membership, "user_id", user.id)),
    }
    # Present either way, so the file has one shape; empty where the module is off.
    payload["notifications"] = (
        _dump(await _rows(session, Notification, "user_id", user.id))
        if is_enabled("notifications")
        else []
    )
    # Metadata only (T-037); the bytes are the user's to download already.
    payload["stored_files"] = (
        _dump(await _rows(session, StoredFile, "uploaded_by", user.id))
        if is_enabled("file_storage")
        else []
    )
    # T-310: ApiKeyPublic, not the row -- `key_hash` has no business here.
    payload["api_keys"] = (
        [
            ApiKeyPublic.model_validate(row).model_dump(mode="json")
            for row in await _rows(session, ApiKey, "created_by", user.id)
        ]
        if is_enabled("public_api")
        else []
    )
    return payload


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
