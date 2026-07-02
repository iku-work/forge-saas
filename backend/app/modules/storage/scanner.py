"""
VirusScanner protocol (T-113): pluggable malware-scanning backend selected by
settings.VIRUS_SCANNER, mirroring backend.py's StorageBackend factory shape
(a Protocol + an lru_cache'd get_*() picker, lazy per-branch imports for test
patchability). app.workers.jobs.virus_scan is the only caller.

Every implementation receives a presigned download URL (the file's bytes
never pass through StorageService -- the scanning job is the only thing that
needs them) and the file's checksum_sha256 (set by confirm_upload from the
backend's ETag), since VirusTotalScanner's hash-first lookup needs the
checksum but not the bytes.

ClamAVScanner speaks clamd's INSTREAM protocol directly over a raw
asyncio socket rather than depending on the (synchronous, unmaintained)
`clamd` PyPI package -- one connect/write/read sequence doesn't justify a new
dependency, the same "no abstraction ahead of need" call app.workers.jobs.email
makes for Resend and app.modules.storage.backend makes for boto3-over-aioboto3.

VirusTotalScanner is an explicit stub per T-113's own Build section ("stub
using VT API for SaaS workflows"): it only ever does a hash lookup
(GET /files/{sha256}) and never uploads+polls for an unknown hash, which a
production-grade implementation would need to. An unknown hash is treated as
clean rather than escalated to a real scan -- acceptable for a stub, not for
a real deployment relying on this scanner.
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
    async def scan(self, *, download_url: str, checksum_sha256: str | None) -> ScanResult: ...


class NoopScanner:
    """Default scanner (settings.VIRUS_SCANNER == "noop"): marks every file
    clean immediately, with no network calls at all."""

    async def scan(
        self,
        *,
        download_url: str,  # noqa: ARG002 -- protocol parameter, unused by design
        checksum_sha256: str | None,  # noqa: ARG002 -- protocol parameter, unused by design
    ) -> ScanResult:
        return ScanResult(clean=True)


class ClamAVScanner:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout
        # Test-only seam: a real scanner never passes this, see
        # app.modules.auth.oauth's _AuthlibOAuthClient for the same pattern.
        self._transport = transport

    async def scan(
        self,
        *,
        download_url: str,
        checksum_sha256: str | None,  # noqa: ARG002 -- ClamAV scans bytes, not hashes
    ) -> ScanResult:
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.get(download_url)
                response.raise_for_status()
                content = response.content
        except httpx.HTTPError as exc:
            raise ScannerError(f"failed to download file for scanning: {exc}") from exc

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
        download_url: str,  # noqa: ARG002 -- VirusTotal stub looks up by hash, never downloads
        checksum_sha256: str | None,
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
            # Unknown hash -- this stub doesn't upload+poll for a verdict (see
            # module docstring); treat as clean.
            return ScanResult(clean=True)
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
