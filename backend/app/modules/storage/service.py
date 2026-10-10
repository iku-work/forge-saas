"""
StorageService (T-112): orchestrates the three-step (presign -> client PUT ->
confirm) upload flow, plan-quota enforcement, and the post-confirm
virus-scan/notification fan-out. Two object-key invariants live here rather
than in the router, since they're security-load-bearing, not presentation:
the key is always server-generated with a UUID component (a client-supplied
`key` is simply not a field PresignUploadRequest defines, so there is nothing
to ignore), and `filename` only ever feeds the human-readable tail of that
key via `_slugify_filename` -- never the lookup itself.

`virus_scan` and `hard_delete_stored_file` are enqueued by name
(app.core.jobs), the same indirection app.modules.gdpr.service uses for its
own ARQ jobs. Both now exist -- app.workers.jobs.virus_scan and
app.workers.jobs.storage respectively -- and both are registered in
WorkerSettings.functions, which is the half that matters: arq resolves these
strings at run time, so an unregistered name is not an import error but a
job that silently never runs. `hard_delete_stored_file` spent its first
release in exactly that state, which is why
tests/test_worker_registration.py now asserts every enqueued name resolves.
"""

import re
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import magic
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import jobs
from app.core.db import persist_then_reject
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    PlanLimitError,
    UnprocessableEntityError,
)
from app.models._mixins import utcnow
from app.modules.audit.service import AuditService
from app.modules.auth.models import User
from app.modules.auth.service import AuthService
from app.modules.billing.dependencies import OrgSubscription
from app.modules.notifications.service import NotificationService
from app.modules.storage.backend import ObjectChangedError, get_storage_backend
from app.modules.storage.keys import delete_every_copy, upload_key
from app.modules.storage.models import StoredFile
from app.modules.storage.quota import check_absolute_ceiling, reservation_cutoff
from app.modules.storage.repository import StoredFileRepository
from app.modules.storage.scan_gate import require_clean_scan
from app.modules.webhooks import service as webhooks
from app.modules.webhooks.events import EventType, FilePayload

_SIZE_TOLERANCE_BYTES = 1024
_DOWNLOAD_EXPIRES_IN_SECONDS = 3600
# Public: app.workers.jobs.storage's reconciliation sweep has to use the
# same window, and a sweep that disagreed with this would delete objects
# whose grace period is still open.
HARD_DELETE_GRACE_PERIOD = timedelta(days=7)
_EXPIRED_PURGE_DEFER = timedelta(minutes=1)
# T-225/ASVS V12.2.1: content-sniffing only needs enough bytes for libmagic's
# signature tables, never the whole object.
_SNIFF_PREFIX_BYTES = 2048
# text/plain and text/csv have no reliable magic-byte signature -- any byte
# sequence can be valid text, so sniffing them can only produce false
# positives. These two stay declared-Content-Type-only, same as before.
_SNIFF_EXEMPT_CONTENT_TYPES = frozenset({"text/plain", "text/csv"})

_DEFAULT_ALLOWED_CONTENT_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/plain",
        "text/csv",
    }
)
# Per-bucket overrides of the default allowlist above (T-112's Build section:
# "configurable per bucket").
_BUCKET_ALLOWED_CONTENT_TYPES: dict[str, frozenset[str]] = {
    "avatars": frozenset({"image/jpeg", "image/png", "image/webp"}),
}

_SLUG_DISALLOWED = re.compile(r"[^a-z0-9]+")


def _slugify_filename(filename: str) -> str:
    stem, dot, ext = filename.rpartition(".")
    if not dot:
        stem, ext = filename, ""
    slug = _SLUG_DISALLOWED.sub("-", stem.lower()).strip("-") or "file"
    ext_slug = _SLUG_DISALLOWED.sub("", ext.lower())
    return f"{slug}.{ext_slug}" if ext_slug else slug


def _allowed_content_types(bucket: str) -> frozenset[str]:
    return _BUCKET_ALLOWED_CONTENT_TYPES.get(bucket, _DEFAULT_ALLOWED_CONTENT_TYPES)


class StorageService:
    def __init__(self, session: AsyncSession, org_id: UUID, redis: Redis) -> None:
        self._session = session
        self._org_id = org_id
        self._repo = StoredFileRepository(session, org_id)
        self._audit = AuditService(session)
        self._notifications = NotificationService(session, redis)
        self._backend = get_storage_backend()

    async def presign_upload(  # noqa: PLR0913 -- matches the T-112 spec's exact param set
        self,
        user_id: UUID,
        *,
        filename: str,
        content_type: str,
        size_bytes: int,
        bucket: str = "default",
        org_sub: OrgSubscription | None,
    ) -> dict[str, Any]:
        if content_type not in _allowed_content_types(bucket):
            raise UnprocessableEntityError(
                "errors.content_type_not_allowed",
                fallback=f"Content type '{content_type}' is not allowed for bucket '{bucket}'",
                params={"content_type": content_type, "bucket": bucket},
            )

        # Before the plan check and outside it: `org_sub` is None wherever
        # billing is off, and that used to mean no size limit whatsoever.
        check_absolute_ceiling(size_bytes)

        # Held until this request commits. Everything from here to the
        # INSERT below is the read-then-write the quota depends on, so two
        # concurrent presigns for one org have to take turns through it --
        # otherwise both read a total that neither has written to yet.
        await self._repo.lock_for_quota_check()

        if org_sub is not None:
            await self._check_quota(size_bytes, org_sub)

        key = f"{self._org_id}/{bucket}/{uuid4()}/{_slugify_filename(filename)}"
        stored_file = await self._repo.create(
            uploaded_by=user_id,
            key=key,
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            bucket=bucket,
            status="pending",
        )
        # The form may write only the upload copy, never `key` itself -- see
        # app.modules.storage.keys for why (review R1). confirm_upload
        # promotes it to `key`.
        presigned = await self._backend.presign_upload(
            upload_key(key), content_type, max_bytes=size_bytes + _SIZE_TOLERANCE_BYTES
        )

        await self._audit.log(
            action="file.presigned",
            resource_type="file",
            resource_id=str(stored_file.id),
            org_id=self._org_id,
            actor_id=user_id,
        )

        return {
            "file_id": stored_file.id,
            "upload_url": presigned.url,
            "fields": presigned.fields,
            "key": presigned.key,
            "expires_at": presigned.expires_at,
        }

    async def _check_quota(self, size_bytes: int, org_sub: OrgSubscription) -> None:
        max_upload_mb = org_sub.plan.features.get("max_upload_mb")
        if max_upload_mb is not None and size_bytes > max_upload_mb * 1_000_000:
            raise PlanLimitError(
                "errors.file_too_large",
                fallback=f"File exceeds the {max_upload_mb}MB per-file limit on your plan",
                params={"max_upload_mb": str(max_upload_mb)},
            )

        storage_gb = org_sub.plan.features.get("storage_gb")
        if storage_gb is not None:
            total_bytes = await self._repo.total_bytes_for_org()
            if total_bytes + size_bytes > storage_gb * 1_000_000_000:
                raise PlanLimitError(
                    "errors.storage_quota_exceeded",
                    fallback=f"This would exceed the {storage_gb}GB storage quota on your plan",
                    params={"storage_gb": str(storage_gb)},
                )

        max_files = org_sub.plan.features.get("max_files")
        if max_files is not None:
            file_count = await self._repo.count_files_for_org()
            if file_count >= max_files:
                raise PlanLimitError(
                    "errors.file_count_quota_exceeded",
                    fallback=f"This would exceed the {max_files}-file limit on your plan",
                    params={"max_files": str(max_files)},
                )

    async def confirm_upload(self, user_id: UUID, file_id: UUID) -> StoredFile:
        stored_file = await self._repo.get_for_update(file_id)
        if stored_file is None:
            raise NotFoundError(
                "errors.resource_not_found",
                fallback="StoredFile not found",
                params={"resource": "StoredFile"},
            )
        if stored_file.status != "pending":
            # Idempotent: re-confirming an already-ready file is a no-op
            # success rather than an error (T-112 Build section).
            return stored_file

        if stored_file.created_at <= reservation_cutoff():
            # The reservation lapsed, so these bytes have already stopped
            # counting toward the quota -- confirming now would add them
            # back without anything having checked there was room. Cheaper
            # to refuse than to re-run the quota check: the client has to
            # presign again anyway, and this is the same verdict
            # `expire_abandoned_uploads` would reach on its next tick, just
            # arrived at sooner. Persisted through the rejection -- raised
            # plainly, the rollback undid the expiry and its audit row, and
            # the purge job then found a live row and did nothing.
            file_id_value = stored_file.id
            await persist_then_reject(
                self._session,
                lambda: self._expire(file_id_value),
                UnprocessableEntityError(
                    "errors.upload_reservation_expired",
                    fallback="This upload was not confirmed in time -- start it again",
                ),
            )

        await self._promote(stored_file)

        if stored_file.content_type not in _SNIFF_EXEMPT_CONTENT_TYPES:
            await self._verify_content_sniff(stored_file)

        # checksum_sha256 is deliberately left unset here. It used to be
        # filled from the object's ETag, which is not a SHA-256 -- for a
        # multipart upload it is not even an MD5 of the content -- so the
        # column said one thing and meant another, and VirusTotalScanner's
        # hash lookup could never match. app.workers.jobs.virus_scan sets it
        # from the bytes it actually reads, which also binds the digest to
        # the object version the verdict was reached on.
        # No cache to invalidate: the row already counted toward the quota
        # as a live reservation, and pending -> ready leaves the total
        # unchanged. That is the point of counting reservations.
        updated = await self._repo.update(file_id, status="ready")

        await jobs.enqueue_job("virus_scan", file_id=updated.id)

        await self._audit.log(
            action="file.uploaded",
            resource_type="file",
            resource_id=str(updated.id),
            org_id=self._org_id,
            actor_id=user_id,
        )
        await self._notifications.send(
            user_id=user_id,
            org_id=self._org_id,
            type="file_uploaded",
            params={"filename": updated.filename},
        )
        # T-320. Emitted here rather than after the virus scan, and the
        # difference matters to a consumer: this fires when the upload is
        # *confirmed*, which is the moment the file exists. A subscriber that
        # cares whether it is clean reads `virus_scan_result` through the API.
        await webhooks.emit(
            self._session,
            self._org_id,
            EventType.FILE_UPLOADED,
            FilePayload(
                file_id=updated.id,
                filename=updated.filename,
                size_bytes=updated.size_bytes,
            ),
        )
        return updated

    async def _promote(self, stored_file: StoredFile) -> None:
        """Copy the upload out of `incoming/` to the key only the server writes.

        Review R1. The upload form can be used again until it expires, so
        nothing about the object at the upload key stays true after it has
        been checked. Everything is therefore checked *after* this copy, on
        the copy: the size here, then the content sniff, then the virus scan.
        Whatever was copied is what gets judged and what gets served.

        The copy is conditioned on the ETag just read, so the size check and
        the copy describe the same object; an overwrite in between fails the
        copy rather than slipping through it. That is a retry for the client,
        not a verdict on the file, so the row stays pending.

        Idempotent, so a confirm that failed after this point can be retried:
        the upload copy is deleted on promotion, and without the check below
        the retry reported "no upload found" for a file sitting at its served
        key. Only this method writes that key, and keys are unique per row, so
        finding it there means an earlier attempt of this confirm put it there
        -- after the size check, which therefore need not run again.
        """
        source = upload_key(stored_file.key)
        metadata = await self._backend.head(source)
        if metadata is None:
            if await self._backend.head(stored_file.key) is not None:
                return
            raise UnprocessableEntityError(
                "errors.file_not_uploaded", fallback="No upload found at the presigned location"
            )

        actual_size = int(metadata["ContentLength"])
        if actual_size > stored_file.size_bytes + _SIZE_TOLERANCE_BYTES:
            await self._backend.delete(source)
            raise UnprocessableEntityError(
                "errors.file_size_mismatch",
                fallback="Uploaded file size does not match the claimed size",
            )

        try:
            await self._backend.copy_if_match(source, stored_file.key, str(metadata["ETag"]))
        except ObjectChangedError as exc:
            raise ConflictError(
                "errors.upload_changed",
                fallback="The upload changed while it was being confirmed -- confirm it again",
            ) from exc
        await self._backend.delete(source)

    async def _verify_content_sniff(self, stored_file: StoredFile) -> None:
        # T-225/ASVS V12.2.1: the declared Content-Type (checked at presign
        # time in presign_upload) is entirely client-supplied -- this
        # confirms the uploaded bytes actually are what was declared, using
        # libmagic's signature tables against the object's real content.
        prefix = await self._backend.read_prefix(stored_file.key, _SNIFF_PREFIX_BYTES)
        sniffed_type = magic.from_buffer(prefix, mime=True)
        if sniffed_type != stored_file.content_type:
            await delete_every_copy(self._backend, stored_file.key)
            raise UnprocessableEntityError(
                "errors.content_type_mismatch",
                fallback=(
                    f"File content does not match its declared type "
                    f"'{stored_file.content_type}' (detected '{sniffed_type}')"
                ),
                params={"declared": stored_file.content_type, "detected": sniffed_type},
            )

    async def list_files(
        self, after: UUID | None, limit: int, *, bucket: str | None = None
    ) -> tuple[list[StoredFile], UUID | None]:
        return await self._repo.list_cursor_filtered(after, limit, bucket=bucket)

    async def uploaders_for(self, files: list[StoredFile]) -> dict[UUID, User]:
        """The users who uploaded a page of files, in one query (T-243)."""
        return await AuthService(self._session).users_by_ids({f.uploaded_by for f in files})

    async def get_download_url(self, file_id: UUID) -> tuple[str, datetime]:
        stored_file = await self._repo.get_or_raise(file_id)
        require_clean_scan(stored_file)
        url = await self._backend.presign_download(
            stored_file.key, _DOWNLOAD_EXPIRES_IN_SECONDS, filename=stored_file.filename
        )
        expires_at = utcnow() + timedelta(seconds=_DOWNLOAD_EXPIRES_IN_SECONDS)
        return url, expires_at

    async def _expire(self, file_id: UUID) -> None:
        """Retire a presign whose reservation lapsed before it was confirmed.

        Soft-deleted rather than hard-deleted so the two existing cleanup
        paths apply unchanged: `hard_delete_stored_file` removes the object
        and the row, and `sweep_orphaned_objects` catches it anyway if that
        job is lost. Enqueued without the seven-day defer `delete_file`
        uses -- that window exists so a *user* can undo a deletion, and
        nobody is going to undo an upload that was never confirmed.

        The object may or may not exist: the client might have uploaded and
        then gone quiet, or never started. `_purge` treats an absent object
        as a success, so both are the same case here.
        """
        await self._repo.soft_delete(file_id)
        # Deferred a minute rather than immediate: this runs inside
        # persist_then_reject, before its commit, and a job that ran first
        # would find the row not yet deleted and skip it.
        await jobs.enqueue_job(
            "hard_delete_stored_file", file_id=file_id, _defer_by=_EXPIRED_PURGE_DEFER
        )
        await self._audit.log(
            action="file.expired",
            resource_type="file",
            resource_id=str(file_id),
            org_id=self._org_id,
            actor_id=None,
        )

    async def delete_file(self, user_id: UUID, file_id: UUID) -> None:
        stored_file = await self._repo.get_or_raise(file_id)
        await self._repo.soft_delete(file_id)

        await jobs.enqueue_job(
            "hard_delete_stored_file",
            file_id=stored_file.id,
            _defer_by=HARD_DELETE_GRACE_PERIOD,
        )

        await self._audit.log(
            action="file.deleted",
            resource_type="file",
            resource_id=str(stored_file.id),
            org_id=self._org_id,
            actor_id=user_id,
        )
