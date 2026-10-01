"""
VirusScanner protocol (T-113): pluggable malware-scanning backend selected by
settings.VIRUS_SCANNER, mirroring backend.py's StorageBackend factory shape
(a Protocol + an lru_cache'd get_*() picker, lazy per-branch imports for test
patchability). app.workers.jobs.virus_scan is the only caller.

Every implementation receives a **ScanSource**: the object as a stream it
can read in chunks, whose SHA-256 is computed from the bytes that go past
(review R4). The bytes never pass through StorageService and are never held
whole -- the job used to read each object into memory, which with twenty
concurrent jobs was a worker-sized OOM waiting for an upload burst. The
digest comes from the same bytes the scanner judged rather than off the row,
so a verdict is bound to the exact content scanned. ClamAVScanner streams the
bytes into clamd with backpressure and ignores the digest; VirusTotalScanner
reads the stream once for the digest and submits nothing.

This used to pass a presigned *download URL* and whatever `checksum_sha256`
happened to be on the row, which confirm_upload filled in from the object's
ETag. An ETag is not a SHA-256 -- for a multipart upload it is not even an
MD5 of the content -- so VirusTotalScanner was looking up a hash that could
never match, and any verdict was against whatever was at the key at the time
rather than against a known object version.

ClamAVScanner speaks clamd's INSTREAM protocol directly over a raw
asyncio socket rather than depending on the (synchronous, unmaintained)
`clamd` PyPI package -- one connect/write/read sequence doesn't justify a new
dependency, the same "no abstraction ahead of need" call app.workers.jobs.email
makes for Resend and app.modules.storage.backend makes for boto3-over-aioboto3.

VirusTotalScanner only ever does a hash lookup (GET /files/{sha256}) and
never uploads+polls for an unknown hash, which a production-grade
implementation would need to. That makes it **reputation enrichment, not a
scanner**: it can tell you a hash is known-bad, and it can never tell you an
unknown hash is safe. An unknown hash therefore raises ScannerError, leaving
the file unscanned (and, per StorageService.get_download_url's gate,
undownloadable) rather than certifying it clean.

It used to return `clean=True` for an unknown hash, which meant every file
nobody had ever submitted to VirusTotal -- that is, every genuinely novel
file, including every targeted one -- was marked clean without being looked
at. app.core.config refuses this scanner outside dev for the same reason it
refuses "noop"; the setting is kept so a deployment can use the lookup as a
cheap known-bad filter in front of a real scanner later.
"""

from __future__ import annotations

import asyncio
import hashlib
import struct
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Protocol

import httpx

from app.core.config import get_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_CLAMD_CHUNK_SIZE = 64 * 1024
# The whole scan, download included: bytes now stream from the object store
# straight into clamd rather than being fetched first, so this also bounds
# how long a slow object store can hold a scan slot. Well inside
# WorkerSettings.job_timeout.
_CLAMD_TIMEOUT_SECONDS = 120.0
# clamd's own replies when a limit stopped it reaching a verdict: the stream
# was longer than StreamMaxLength, or (with AlertExceedsMax on) an archive
# expanded past MaxScanSize / MaxFiles / MaxRecursion.
_CLAMD_SIZE_LIMIT_REPLY = "size limit exceeded"
_CLAMD_LIMITS_SIGNATURE = "Heuristics.Limits.Exceeded"
_VIRUSTOTAL_FILES_URL = "https://www.virustotal.com/api/v3/files/"
_HTTP_NOT_FOUND = 404
_HTTP_OK = 200


class ScanLimitExceededError(Exception):
    """The scanner stopped before reaching a verdict because a limit was hit.

    Permanent, unlike ScannerError: the file will be exactly as large, or as
    deeply nested, on the next attempt. The job marks it "failed" -- no
    verdict, not downloadable -- and never "clean". clamd's default is the
    opposite: with AlertExceedsMax off, an archive that expands past its
    limits is reported OK, so a zip bomb scans clean (review R4, verified
    against clamav/clamav:stable with clamconf). The compose files turn
    AlertExceedsMax on; this is what the resulting reply maps to.
    """


class ScannerError(Exception):
    """Raised by a scanner on a transient or permanent failure to obtain a
    verdict -- never for a clean/infected result, which is a successful scan.
    app.workers.jobs.virus_scan retries on this up to 3x with backoff."""


@dataclass(frozen=True)
class ScanResult:
    clean: bool
    signature: str | None = None  # set when clean is False


class ScanSource(Protocol):
    """The object being scanned, as a stream that can be read more than once.

    Review R4. The job used to read the whole object into memory and hand
    the scanner a `bytes`; with twenty concurrent jobs and a 100 MiB upload
    ceiling that is ~2 GiB of file data in a 768 MiB worker. A source is
    read in chunks and never held whole.

    `chunks()` starts a fresh pass every time it is called -- a retried scan
    re-streams the object rather than replaying a buffer. `sha256` is the
    digest of the last pass that read to the end, or None if none has.
    """

    def chunks(self) -> AsyncIterator[bytes]: ...

    @property
    def sha256(self) -> str | None: ...


class BytesSource:
    """A ScanSource over bytes already in memory -- for tests and dev tools,
    never for an uploaded object (that is the job's streaming source)."""

    def __init__(self, content: bytes, *, chunk_size: int = _CLAMD_CHUNK_SIZE) -> None:
        self._content = content
        self._chunk_size = chunk_size
        self._sha256: str | None = None

    async def chunks(self) -> AsyncIterator[bytes]:
        digest = hashlib.sha256()
        for offset in range(0, len(self._content), self._chunk_size):
            chunk = self._content[offset : offset + self._chunk_size]
            digest.update(chunk)
            yield chunk
        self._sha256 = digest.hexdigest()

    @property
    def sha256(self) -> str | None:
        return self._sha256


class VirusScanner(Protocol):
    async def scan(self, source: ScanSource) -> ScanResult: ...


class NoopScanner:
    """Default scanner (settings.VIRUS_SCANNER == "noop"): marks every file
    clean immediately, with no network calls at all.

    Dev only -- app.core.config raises at startup if this is selected outside
    dev, because it certifies malware clean by construction."""

    async def scan(
        self,
        source: ScanSource,  # noqa: ARG002 -- protocol parameter, unused by design
    ) -> ScanResult:
        return ScanResult(clean=True)


class ClamAVScanner:
    def __init__(self, *, host: str, port: int, timeout: float = _CLAMD_TIMEOUT_SECONDS) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout

    async def scan(self, source: ScanSource) -> ScanResult:
        try:
            reply = await asyncio.wait_for(self._instream(source), timeout=self._timeout)
        except (OSError, TimeoutError) as exc:
            raise ScannerError(f"clamd connection failed: {exc}") from exc

        return _parse_clamd_reply(reply)

    async def _instream(self, source: ScanSource) -> str:
        reader, writer = await asyncio.open_connection(self._host, self._port)
        try:
            writer.write(b"zINSTREAM\0")
            try:
                async for chunk in source.chunks():
                    for offset in range(0, len(chunk), _CLAMD_CHUNK_SIZE):
                        piece = chunk[offset : offset + _CLAMD_CHUNK_SIZE]
                        writer.write(struct.pack("!L", len(piece)) + piece)
                        # Backpressure, per chunk (review R4). Draining once
                        # at the end let the whole object pile up in the
                        # transport's write buffer before a byte had left.
                        await writer.drain()
                writer.write(struct.pack("!L", 0))  # zero-length chunk terminates the stream
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                # clamd hangs up mid-stream when StreamMaxLength is passed,
                # after writing why. The reply is the useful part.
                pass
            data = await reader.read()
            return data.decode("utf-8", errors="replace").strip("\0").strip()
        finally:
            writer.close()
            await writer.wait_closed()


def _parse_clamd_reply(reply: str) -> ScanResult:
    # clamd INSTREAM replies: "stream: OK", "stream: <signature> FOUND", or
    # "stream: <message> ERROR".
    body = reply.removeprefix("stream:").strip()
    if body == "OK":
        return ScanResult(clean=True)
    if body.endswith("FOUND"):
        signature = body.removesuffix("FOUND").strip()
        if signature.startswith(_CLAMD_LIMITS_SIGNATURE):
            # Not malware -- clamd gave up. See ScanLimitExceededError.
            raise ScanLimitExceededError(f"clamd stopped at a scan limit: {signature}")
        return ScanResult(clean=False, signature=signature)
    if _CLAMD_SIZE_LIMIT_REPLY in body.lower():
        raise ScanLimitExceededError(f"clamd refused the stream: {reply}")
    raise ScannerError(f"clamd error: {reply}")


class VirusTotalScanner:
    def __init__(
        self,
        *,
        api_key: str,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout
        # Test-only seam: a real scanner never passes this, see
        # app.modules.auth.oauth's _AuthlibOAuthClient for the same pattern.
        self._transport = transport

    async def scan(self, source: ScanSource) -> ScanResult:
        # A hash lookup needs the digest, and the digest needs one full pass.
        # The bytes themselves are never submitted anywhere.
        async for _chunk in source.chunks():
            pass
        checksum_sha256 = source.sha256
        if not checksum_sha256:
            raise ScannerError("VirusTotalScanner requires a checksum_sha256 to look up")

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.get(
                    f"{_VIRUSTOTAL_FILES_URL}{checksum_sha256}",
                    headers={"x-apikey": self._api_key},
                )
        except httpx.HTTPError as exc:
            raise ScannerError(f"VirusTotal request failed: {exc}") from exc

        if response.status_code == _HTTP_NOT_FOUND:
            # Unknown hash. VirusTotal has never seen this file, which is the
            # expected answer for every *novel* file -- so it is evidence of
            # nothing, and certainly not of safety. Fail closed: the caller
            # marks the file "failed", which leaves it undownloadable.
            raise ScannerError(
                f"VirusTotal has no record of {checksum_sha256} -- "
                f"a hash lookup cannot establish that an unknown file is clean"
            )
        if response.status_code != _HTTP_OK:
            raise ScannerError(f"VirusTotal returned HTTP {response.status_code}")

        stats = response.json()["data"]["attributes"]["last_analysis_stats"]
        flagged = stats.get("malicious", 0) + stats.get("suspicious", 0)
        if flagged == 0:
            return ScanResult(clean=True)
        return ScanResult(clean=False, signature=f"virustotal:{flagged}_engines_flagged")


@lru_cache
def get_virus_scanner() -> VirusScanner:
    settings = get_settings()
    if settings.VIRUS_SCANNER == "clamav":
        return ClamAVScanner(host=settings.CLAMAV_HOST, port=settings.CLAMAV_PORT)
    if settings.VIRUS_SCANNER == "virustotal":
        return VirusTotalScanner(api_key=settings.VIRUSTOTAL_API_KEY)
    return NoopScanner()
