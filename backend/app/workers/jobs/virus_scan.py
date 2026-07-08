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

T-206: `_scan` wraps the single scanner.scan() call in
app.core.resilience's `@resilient("virus_scanner")` -- a ScannerError is
always treated as transient (see is_transient_error), so it's retried inline
a few times exactly like the old hand-rolled backoff loop was. A scan that
completes but finds malware is not a retry case; it's the (correct) infected
outcome. Once the shared "virus_scanner" breaker has actually tripped
(repeated scanner failures across *many* files, not just this one), the
ExternalServiceUnavailableError it raises becomes an arq.worker.Retry here --
the file stays "ready" (pending scan, undownloadable) and the whole job
re-queues, per T-206's "Virus scanner down -- uploads stay pending_scan and
undownloadable; scan job retries" contract. Only a scanner failure that
exhausts its retries *without* tripping the breaker marks the file "failed"
(status, not virus_scan_result -- distinct from "infected", which means a
verdict *was* reached) and is audit-logged, per T-113's own spec.
"""

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

import sentry_sdk
from arq.worker import Retry

from app.core.db import async_session_factory
from app.core.exceptions import ExternalServiceUnavailableError
from app.core.resilience import resilient
from app.core.tracing import trace_job
from app.modules.audit.service import AuditService
from app.modules.notifications.service import NotificationService
from app.modules.storage.backend import get_storage_backend
from app.modules.storage.models import StoredFile
from app.modules.storage.scanner import ScannerError, ScanResult, VirusScanner, get_virus_scanner

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

_DOWNLOAD_EXPIRES_IN_SECONDS = 300  # the scan window
# >= app.core.resilience's reset_timeout (30s): re-queuing sooner than the
# breaker could possibly have recovered would just spend an ARQ retry on
# another guaranteed CircuitBreakerError.
_VIRUS_SCANNER_RETRY_DEFER_SECONDS = 30


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

        audit = AuditService(session)

        try:
            result = await _scan(
                scanner, download_url=download_url, checksum_sha256=stored_file.checksum_sha256
            )
        except ExternalServiceUnavailableError:
            # The "virus_scanner" breaker is open -- re-queue the whole job
            # rather than marking the file failed; see module docstring.
            raise Retry(defer=_VIRUS_SCANNER_RETRY_DEFER_SECONDS) from None
        except ScannerError as exc:
            logger.exception("virus_scan: scanner failed for file_id=%s", file_id)
            sentry_sdk.capture_exception(exc)
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


@resilient("virus_scanner")
async def _scan(
    scanner: VirusScanner, *, download_url: str, checksum_sha256: str | None
) -> ScanResult:
    return await scanner.scan(download_url=download_url, checksum_sha256=checksum_sha256)
