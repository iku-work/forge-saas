"""
ARQ job (T-113): scans a confirmed upload (StoredFile.status == "ready") via
the configured VirusScanner (app.modules.storage.scanner, selected by
settings.VIRUS_SCANNER) and resolves it to "ready"+clean or "infected".
StorageService.confirm_upload (T-112) enqueues this job by name right after
setting status="ready" -- see that service's own module docstring for why the
forward reference (this function didn't exist yet when T-112 landed) is safe.

Loads StoredFile via a direct `session.get` rather than StoredFileRepository
-- like app.workers.jobs.gdpr's purge_user_data, this is a system job with no
acting user/org context to scope a repository to, not an HTTP request handler.

Retries up to _MAX_ATTEMPTS times with exponential backoff on a ScannerError
(network/protocol failure talking to the scanner) -- same shape as
app.workers.jobs.email's Resend retry loop. A scan that completes but finds
malware is not a retry case; it's the (correct) infected outcome. A final
scanner failure marks the file "failed" (status, not virus_scan_result --
distinct from "infected", which means a verdict *was* reached) and is
audit-logged, per T-113's own spec.
"""

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

import sentry_sdk

from app.core.db import async_session_factory
from app.core.tracing import trace_job
from app.modules.audit.service import AuditService
from app.modules.notifications.service import NotificationService
from app.modules.storage.backend import get_storage_backend
from app.modules.storage.models import StoredFile
from app.modules.storage.scanner import ScannerError, ScanResult, VirusScanner, get_virus_scanner

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 1.0
_DOWNLOAD_EXPIRES_IN_SECONDS = 300  # the scan window


@trace_job
async def virus_scan(
    ctx: dict[str, Any],
    *,
    file_id: UUID,
) -> None:
    redis: Redis = ctx["redis"]
    backend = get_storage_backend()
    scanner = get_virus_scanner()

    async with async_session_factory() as session:
        stored_file = await session.get(StoredFile, file_id)
        if stored_file is None or stored_file.status != "ready":
            logger.info("virus_scan: file_id=%s not pending scan, skipping", file_id)
            return

        download_url = await backend.presign_download(stored_file.key, _DOWNLOAD_EXPIRES_IN_SECONDS)

        result = await _scan_with_retries(
            scanner,
            download_url=download_url,
            checksum_sha256=stored_file.checksum_sha256,
            file_id=file_id,
        )

        audit = AuditService(session)

        if result is None:
            stored_file.status = "failed"
            await audit.log(
                action="file.scan_failed",
                resource_type="file",
                resource_id=str(file_id),
                org_id=stored_file.org_id,
            )
            await session.commit()
            return

        if result.clean:
            stored_file.virus_scan_result = "clean"
            await session.commit()
            return

        stored_file.status = "infected"
        stored_file.virus_scan_result = result.signature or "infected"
        await backend.delete(stored_file.key)

        await audit.log(
            action="file.infected",
            resource_type="file",
            resource_id=str(file_id),
            org_id=stored_file.org_id,
        )
        notifications = NotificationService(session, redis)
        await notifications.send(
            user_id=stored_file.uploaded_by,
            org_id=stored_file.org_id,
            type="file_infected",
            title="A file you uploaded was flagged as infected",
            body=f"{stored_file.filename} was removed after failing a virus scan.",
        )
        await session.commit()


async def _scan_with_retries(
    scanner: VirusScanner,
    *,
    download_url: str,
    checksum_sha256: str | None,
    file_id: UUID,
) -> ScanResult | None:  # None once retries are exhausted
    last_exc: ScannerError | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return await scanner.scan(download_url=download_url, checksum_sha256=checksum_sha256)
        except ScannerError as exc:
            last_exc = exc
            if attempt < _MAX_ATTEMPTS:
                await asyncio.sleep(_BACKOFF_BASE_SECONDS * 2 ** (attempt - 1))

    logger.error(
        "virus_scan: scanner failed after %d attempt(s) (file_id=%s): %s",
        _MAX_ATTEMPTS,
        file_id,
        last_exc,
    )
    if last_exc is not None:
        sentry_sdk.capture_exception(last_exc)
    return None
