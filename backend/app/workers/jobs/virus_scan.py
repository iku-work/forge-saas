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

import hashlib
import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx
import sentry_sdk
from arq.worker import Retry

from app.core.db import async_session_factory
from app.core.exceptions import ExternalServiceUnavailableError
from app.core.resilience import resilient
from app.core.tracing import trace_job
from app.modules.audit.service import AuditService
from app.modules.notifications.service import NotificationService
from app.modules.storage.backend import StorageBackend, get_storage_backend
from app.modules.storage.models import StoredFile
from app.modules.storage.scanner import ScannerError, ScanResult, VirusScanner, get_virus_scanner

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

_DOWNLOAD_EXPIRES_IN_SECONDS = 300  # the scan window
_DOWNLOAD_TIMEOUT_SECONDS = 60.0
# Bounds the worker's memory per scan. Deliberately a hard ceiling rather than
# a plan feature: a file too big to scan must not become a file that skips
# scanning, so anything over this is "failed" (undownloadable), not "clean".
# Raising it means raising the worker's memory budget with it.
_MAX_SCAN_BYTES = 256 * 1024 * 1024
# >= app.core.resilience's reset_timeout (30s): re-queuing sooner than the
# breaker could possibly have recovered would just spend an ARQ retry on
# another guaranteed CircuitBreakerError.
_VIRUS_SCANNER_RETRY_DEFER_SECONDS = 30


class ScanTooLargeError(Exception):
    """The object is too big to scan in memory -- permanent, not transient.

    Separate from ScannerError on purpose: is_transient_error classifies
    every ScannerError as retryable (and so excludes it from the breaker's
    failure count), which is right for "clamd refused the connection" and
    wrong for "this file is 4GB".
    """


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

        audit = AuditService(session)

        try:
            content = await _fetch(backend, stored_file.key, stored_file.size_bytes)
            checksum = hashlib.sha256(content).hexdigest()
            result = await _scan(scanner, content=content, checksum_sha256=checksum)
        except ExternalServiceUnavailableError:
            # Either breaker in play here is open -- "virus_scanner" (clamd
            # is down) or "s3" (the object store is). Both mean "we cannot
            # get a verdict right now", which is a re-queue, not a verdict;
            # see the module docstring.
            raise Retry(defer=_VIRUS_SCANNER_RETRY_DEFER_SECONDS) from None
        except (ScannerError, ScanTooLargeError) as exc:
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
            # Written here rather than in confirm_upload, and only now: this
            # is the digest of the bytes that were actually scanned, so the
            # column and the verdict describe the same object version.
            stored_file.checksum_sha256 = checksum
            stored_file.virus_scan_result = "clean"
            await session.commit()
            return

        stored_file.status = "infected"
        stored_file.checksum_sha256 = checksum  # forensics: the object is about to be deleted
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
            params={"filename": stored_file.filename},
        )
        await session.commit()


async def _fetch(backend: StorageBackend, key: str, claimed_size_bytes: int) -> bytes:
    """The object's bytes, once, for both hashing and scanning.

    Refuses anything over _MAX_SCAN_BYTES *before* reading it.
    `claimed_size_bytes` is confirm_upload's already-verified size (it heads
    the object and rejects a mismatch), so this can decline without a fetch.
    """
    if claimed_size_bytes > _MAX_SCAN_BYTES:
        raise ScanTooLargeError(
            f"object is {claimed_size_bytes} bytes, over the {_MAX_SCAN_BYTES}-byte scan limit"
        )
    download_url = await backend.presign_download(key, _DOWNLOAD_EXPIRES_IN_SECONDS)
    return await _download(download_url)


@resilient("s3")
async def _download(
    download_url: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bytes:
    """Streamed, size-bounded read of a presigned URL.

    Bounded because the old code did `response.content` on an unbounded
    download inside ClamAVScanner: one large upload could exhaust the
    worker's memory and, with max_jobs=20 in a single process, take every
    other job with it.

    Under `@resilient("s3")` so a blip fetching the object is retried rather
    than condemning the file -- which is what ClamAVScanner's download got
    for free when it lived inside the `@resilient("virus_scanner")` call.
    ScanTooLargeError is deliberately not a ScannerError: is_transient_error
    treats every ScannerError as retryable, and a file that is too big will
    still be too big three attempts later.

    `transport` is the same test-only seam app.modules.auth.oauth's
    _AuthlibOAuthClient uses; the job never passes it.
    """
    try:
        async with (
            httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT_SECONDS, transport=transport) as client,
            client.stream("GET", download_url) as response,
        ):
            response.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > _MAX_SCAN_BYTES:
                    # The size the row claims and the size the object actually
                    # is are different facts; this loop is what bounds memory.
                    raise ScanTooLargeError(
                        f"object exceeded the {_MAX_SCAN_BYTES}-byte scan limit"
                    )
                chunks.append(chunk)
    except httpx.HTTPError as exc:
        raise ScannerError(f"failed to download file for scanning: {exc}") from exc

    return b"".join(chunks)


@resilient("virus_scanner")
async def _scan(scanner: VirusScanner, *, content: bytes, checksum_sha256: str) -> ScanResult:
    return await scanner.scan(content=content, checksum_sha256=checksum_sha256)
