"""
ARQ job (T-113): scans a confirmed upload (StoredFile.status == "ready") via
the configured VirusScanner (app.modules.storage.scanner, selected by
settings.VIRUS_SCANNER) and resolves it to "ready"+clean or "infected".
StorageService.confirm_upload (T-112) enqueues this job by name right after
setting status="ready".

Loads StoredFile via a direct `session.get` rather than StoredFileRepository
-- like app.workers.jobs.gdpr's purge_user_data, this is a system job with no
acting user/org context to scope a repository to, not an HTTP request handler.

Review R4 (2026-09-29) changed three things about how it runs:

- **The object is streamed, never held.** It used to be read whole into
  memory, joined into a second full copy, and handed to a ClamAV client that
  wrote every chunk before its first drain. With twenty concurrent jobs and
  a 100 MiB upload ceiling, that is ~2 GiB of file data in a 768 MiB worker:
  one burst of uploads could OOM the process that also sends email and
  deletes data. Now `_ObjectSource` streams from the object store, hashes as
  it goes, and the scanner applies backpressure per chunk.
- **No database connection is held across the I/O.** The row is read, the
  session closed, the object scanned, and the verdict written in a fresh
  session with a conditional UPDATE. Under pgbouncer transaction pooling an
  open transaction pins a server connection, and this one used to stay open
  for the whole download and scan.
- **It runs on its own queue** (app.core.jobs.SCAN_QUEUE_NAME), consumed by
  ScanWorkerSettings in its own container, so a burst of scans occupies
  scan slots rather than the slots email and deletion need.

T-206: `_scan` wraps the scanner call in `@resilient("virus_scanner")` -- a
ScannerError is treated as transient and retried inline a few times. A scan
that completes but finds malware is not a retry case; it is the (correct)
infected outcome. Once the shared "virus_scanner" or "s3" breaker has
tripped, the ExternalServiceUnavailableError becomes an arq Retry -- the file
stays "ready" (pending scan, undownloadable) and the whole job re-queues.
A scanner failure that exhausts its retries without tripping the breaker,
and every *permanent* failure (too large to scan, clamd stopped at a limit),
marks the file "failed": no verdict, never downloadable, audit-logged.
"""

import hashlib
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

import httpx
import sentry_sdk
from arq.worker import Retry
from redis.asyncio import Redis
from sqlalchemy import CursorResult, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import async_session_factory
from app.core.exceptions import ExternalServiceUnavailableError
from app.core.resilience import resilient
from app.core.tracing import trace_job
from app.modules.audit.service import AuditService
from app.modules.notifications.service import NotificationService
from app.modules.storage.backend import StorageBackend, get_storage_backend
from app.modules.storage.keys import delete_every_copy
from app.modules.storage.models import StoredFile
from app.modules.storage.scanner import (
    ScanLimitExceededError,
    ScannerError,
    ScanResult,
    ScanSource,
    VirusScanner,
    get_virus_scanner,
)

logger = logging.getLogger(__name__)

_DOWNLOAD_EXPIRES_IN_SECONDS = 300  # the scan window
_DOWNLOAD_TIMEOUT_SECONDS = 60.0
_STREAM_CHUNK_BYTES = 64 * 1024
# A hard ceiling rather than a plan feature: a file too big to scan must not
# become a file that skips scanning, so anything over this is "failed"
# (undownloadable), not "clean". Streaming means this no longer bounds
# memory -- it bounds how long one object can occupy a scan slot.
_MAX_SCAN_BYTES = 256 * 1024 * 1024
# >= app.core.resilience's reset_timeout (30s): re-queuing sooner than the
# breaker could possibly have recovered would just spend an ARQ retry on
# another guaranteed CircuitBreakerError.
_VIRUS_SCANNER_RETRY_DEFER_SECONDS = 30


class ScanTooLargeError(Exception):
    """The object is over _MAX_SCAN_BYTES -- permanent, not transient.

    Separate from ScannerError on purpose: is_transient_error classifies
    every ScannerError as retryable (and so excludes it from the breaker's
    failure count), which is right for "clamd refused the connection" and
    wrong for "this file is 4GB".
    """


_PERMANENT_FAILURES = (ScannerError, ScanTooLargeError, ScanLimitExceededError)


@dataclass(frozen=True)
class _ScanTarget:
    """What the job needs from the row, read before the session closes."""

    file_id: UUID
    key: str
    size_bytes: int
    org_id: UUID
    uploaded_by: UUID
    filename: str


class _ObjectSource:
    """A ScanSource streaming one stored object from the object store.

    Every `chunks()` call is a fresh presigned GET, so a retried scan reads
    the object again rather than keeping a copy of it. The digest is of the
    bytes that went past, which are the bytes the scanner judged.

    `transport` is the same test-only seam app.modules.auth.oauth's
    _AuthlibOAuthClient uses; the job never passes it.
    """

    def __init__(
        self,
        backend: StorageBackend,
        key: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._backend = backend
        self._key = key
        self._transport = transport
        self._sha256: str | None = None

    @property
    def sha256(self) -> str | None:
        return self._sha256

    async def chunks(self) -> AsyncIterator[bytes]:
        self._sha256 = None
        url = await self._backend.presign_download(self._key, _DOWNLOAD_EXPIRES_IN_SECONDS)
        digest = hashlib.sha256()
        total = 0
        try:
            async with (
                httpx.AsyncClient(
                    timeout=_DOWNLOAD_TIMEOUT_SECONDS, transport=self._transport
                ) as client,
                client.stream("GET", url) as response,
            ):
                response.raise_for_status()
                async for chunk in response.aiter_bytes(_STREAM_CHUNK_BYTES):
                    total += len(chunk)
                    if total > _MAX_SCAN_BYTES:
                        # The size the row claims and the size the object
                        # actually is are different facts; this is the one
                        # that ends the read.
                        raise ScanTooLargeError(
                            f"object exceeded the {_MAX_SCAN_BYTES}-byte scan limit"
                        )
                    digest.update(chunk)
                    yield chunk
        except httpx.HTTPError as exc:
            # Transient by classification, so @resilient("virus_scanner")
            # retries it rather than condemning the file over a blip.
            raise ScannerError(f"failed to read the object for scanning: {exc}") from exc
        self._sha256 = digest.hexdigest()


def _open_source(backend: StorageBackend, key: str) -> ScanSource:
    """The seam tests replace to scan canned bytes without an object store."""
    return _ObjectSource(backend, key)


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
        status = stored_file.status if stored_file is not None else None
        if stored_file is None or status not in {"ready", "infected"}:
            logger.info("virus_scan: file_id=%s not pending scan, skipping", file_id)
            return
        target = _ScanTarget(
            file_id=stored_file.id,
            key=stored_file.key,
            size_bytes=stored_file.size_bytes,
            org_id=stored_file.org_id,
            uploaded_by=stored_file.uploaded_by,
            filename=stored_file.filename,
        )
    # From here to the verdict, no connection is held (review R4).

    if status == "infected":
        # An earlier attempt committed the verdict and failed before the
        # object was gone. Finish that; everything else is already recorded.
        await delete_every_copy(backend, target.key)
        return

    try:
        if target.size_bytes > _MAX_SCAN_BYTES:
            # confirm_upload has already checked the object's real size
            # against this claim, so this can decline without a read.
            raise ScanTooLargeError(
                f"object is {target.size_bytes} bytes, over the {_MAX_SCAN_BYTES}-byte scan limit"
            )
        source = _open_source(backend, target.key)
        result = await _scan(scanner, source)
        if source.sha256 is None:
            # A scanner that decided without reading (the dev noop) still
            # leaves a real digest in the column, not a gap.
            async for _chunk in source.chunks():
                pass
    except ExternalServiceUnavailableError:
        # Either breaker in play here is open -- "virus_scanner" (clamd is
        # down) or "s3" (the object store is). Both mean "we cannot get a
        # verdict right now", which is a re-queue, not a verdict.
        raise Retry(defer=_VIRUS_SCANNER_RETRY_DEFER_SECONDS) from None
    except _PERMANENT_FAILURES as exc:
        logger.exception("virus_scan: scanner failed for file_id=%s", file_id)
        sentry_sdk.capture_exception(exc)
        await _record_failure(target)
        return

    checksum = source.sha256
    if result.clean:
        # The digest of the bytes that were actually scanned, so the column
        # and the verdict describe the same object -- which, since review
        # R1, is also the object that is served.
        async with async_session_factory() as session:
            await _write_verdict(session, target, checksum=checksum, verdict="clean")
            await session.commit()
        return

    await _record_infected(target, checksum=checksum, result=result, backend=backend, redis=redis)


async def _write_verdict(
    session: AsyncSession,
    target: _ScanTarget,
    *,
    checksum: str | None,
    verdict: str,
    status: str = "ready",
) -> bool:
    """Write the verdict, in the caller's transaction, if the row is still
    the one that was scanned.

    Conditional, because nothing held the row during the scan: a file
    deleted (or already resolved) in the meantime is left as it is, and
    returns False.
    """
    model = cast("Any", StoredFile)
    result = await session.execute(
        update(StoredFile)
        .where(
            model.id == target.file_id,
            model.status == "ready",
            model.deleted_at.is_(None),
        )
        .values(status=status, checksum_sha256=checksum, virus_scan_result=verdict)
    )
    written = cast("CursorResult[Any]", result).rowcount == 1
    if not written:
        logger.info(
            "virus_scan: file_id=%s changed during the scan, verdict %s not recorded",
            target.file_id,
            verdict,
        )
    return written


async def _record_failure(target: _ScanTarget) -> None:
    async with async_session_factory() as session:
        model = cast("Any", StoredFile)
        await session.execute(
            update(StoredFile)
            .where(
                model.id == target.file_id,
                model.status == "ready",
                model.deleted_at.is_(None),
            )
            .values(status="failed")
        )
        await AuditService(session).log(
            action="file.scan_failed",
            resource_type="file",
            resource_id=str(target.file_id),
            org_id=target.org_id,
        )
        await session.commit()


async def _record_infected(
    target: _ScanTarget,
    *,
    checksum: str | None,
    result: ScanResult,
    backend: StorageBackend,
    redis: Redis,
) -> None:
    # The verdict, its audit event and the uploader's notification commit
    # together, *then* the object goes. The other order lost all three when
    # the write failed after the delete: the retry could no longer read the
    # object and recorded a scanner failure instead of malware. This order's
    # failure is a committed verdict with the object still present -- which
    # the download gate already refuses, and which the retry finishes (see
    # the top of virus_scan).
    async with async_session_factory() as session:
        written = await _write_verdict(
            session,
            target,
            # forensics: the object is about to go; the digest is what stays
            checksum=checksum,
            verdict=result.signature or "infected",
            status="infected",
        )
        if written:
            await AuditService(session).log(
                action="file.infected",
                resource_type="file",
                resource_id=str(target.file_id),
                org_id=target.org_id,
            )
            await NotificationService(session, redis).send(
                user_id=target.uploaded_by,
                org_id=target.org_id,
                type="file_infected",
                params={"filename": target.filename},
            )
        await session.commit()

    # Whether or not the row was still there: it is malware either way. Every
    # copy, including anything left at the upload key.
    await delete_every_copy(backend, target.key)


@resilient("virus_scanner")
async def _scan(scanner: VirusScanner, source: ScanSource) -> ScanResult:
    return await scanner.scan(source)
