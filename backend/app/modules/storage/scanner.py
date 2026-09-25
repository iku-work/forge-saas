"""
VirusScanner protocol (T-113): pluggable malware-scanning backend selected by
settings.VIRUS_SCANNER, mirroring backend.py's StorageBackend factory shape
(a Protocol + an lru_cache'd get_*() picker, lazy per-branch imports for test
patchability). app.workers.jobs.virus_scan is the only caller.

Every implementation receives the object's **bytes** and their SHA-256 digest,
both produced by app.workers.jobs.virus_scan in a single fetch. The bytes
never pass through StorageService -- the scanning job is the only thing that
needs them -- and the digest is computed from those same bytes rather than
read off the row, so a verdict is always bound to the exact content that was
scanned. ClamAVScanner needs the bytes and ignores the digest;
VirusTotalScanner's hash lookup needs the digest and ignores the bytes.

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
import struct
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import httpx

from app.core.config import get_settings

_CLAMD_CHUNK_SIZE = 8192
_VIRUSTOTAL_FILES_URL = "https://www.virustotal.com/api/v3/files/"
_HTTP_NOT_FOUND = 404
_HTTP_OK = 200


class ScannerError(Exception):
    """Raised by a scanner on a transient or permanent failure to obtain a
    verdict -- never for a clean/infected result, which is a successful scan.
    app.workers.jobs.virus_scan retries on this up to 3x with backoff."""


@dataclass(frozen=True)
class ScanResult:
    clean: bool
    signature: str | None = None  # set when clean is False


class VirusScanner(Protocol):
    async def scan(self, *, content: bytes, checksum_sha256: str) -> ScanResult: ...


class NoopScanner:
    """Default scanner (settings.VIRUS_SCANNER == "noop"): marks every file
    clean immediately, with no network calls at all.

    Dev only -- app.core.config raises at startup if this is selected outside
    dev, because it certifies malware clean by construction."""

    async def scan(
        self,
        *,
        content: bytes,  # noqa: ARG002 -- protocol parameter, unused by design
        checksum_sha256: str,  # noqa: ARG002 -- protocol parameter, unused by design
    ) -> ScanResult:
        return ScanResult(clean=True)


class ClamAVScanner:
    def __init__(self, *, host: str, port: int, timeout: float = 30.0) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout

    async def scan(
        self,
        *,
        content: bytes,
        checksum_sha256: str,  # noqa: ARG002 -- ClamAV scans bytes, not hashes
    ) -> ScanResult:
        # The fetch that produced `content` lives in the calling job, which
        # needs the same bytes to compute checksum_sha256 -- doing it here as
        # well would mean downloading every object twice.
        try:
            reply = await asyncio.wait_for(self._instream(content), timeout=self._timeout)
        except (OSError, TimeoutError) as exc:
            raise ScannerError(f"clamd connection failed: {exc}") from exc

        return _parse_clamd_reply(reply)

    async def _instream(self, content: bytes) -> str:
        reader, writer = await asyncio.open_connection(self._host, self._port)
        try:
            writer.write(b"zINSTREAM\0")
            for offset in range(0, len(content), _CLAMD_CHUNK_SIZE):
                chunk = content[offset : offset + _CLAMD_CHUNK_SIZE]
                writer.write(struct.pack("!L", len(chunk)) + chunk)
            writer.write(struct.pack("!L", 0))  # zero-length chunk terminates the stream
            await writer.drain()
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
        return ScanResult(clean=False, signature=body.removesuffix("FOUND").strip())
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

    async def scan(
        self,
        *,
        content: bytes,  # noqa: ARG002 -- looks up by hash, never submits the bytes
        checksum_sha256: str,
    ) -> ScanResult:
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
