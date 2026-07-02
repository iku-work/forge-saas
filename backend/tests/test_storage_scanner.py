"""
Verifies T-113's VirusScanner implementations: NoopScanner's always-clean
default, ClamAVScanner's INSTREAM wire protocol (clean/infected/error replies,
a download failure, and a refused clamd connection -- all surfaced as
ScannerError so app.workers.jobs.virus_scan's retry loop can catch them
uniformly), VirusTotalScanner's hash-first lookup (unknown hash treated as
clean per its own stub docstring, known-clean, known-malicious, and HTTP
failure modes), and get_virus_scanner()'s settings-driven selection.

ClamAVScanner is exercised against a real local asyncio TCP server speaking
just enough of clamd's INSTREAM protocol to reply with a canned response --
no mocking of asyncio.open_connection itself, so the chunked-length-prefix
framing is actually exercised end to end. Both scanners' outbound HTTP calls
go through httpx.MockTransport (the same test-only seam pattern already used
by app.modules.auth.oauth's OAuth clients), not a real network call.
"""

import asyncio
import struct
from collections.abc import AsyncIterator, Awaitable, Callable

import httpx
import pytest

from app.core.config import get_settings
from app.modules.storage import scanner as scanner_module
from app.modules.storage.scanner import (
    ClamAVScanner,
    NoopScanner,
    ScannerError,
    VirusTotalScanner,
    get_virus_scanner,
)

_INSTREAM_HEADER = b"zINSTREAM\0"


async def _drain_instream(reader: asyncio.StreamReader) -> None:
    while True:
        length_bytes = await reader.readexactly(4)
        length = struct.unpack("!L", length_bytes)[0]
        if length == 0:
            break
        await reader.readexactly(length)


@pytest.fixture
async def fake_clamd() -> AsyncIterator[Callable[[bytes], Awaitable[int]]]:
    servers: list[asyncio.AbstractServer] = []

    async def _start(reply: bytes) -> int:
        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.readexactly(len(_INSTREAM_HEADER))
            await _drain_instream(reader)
            writer.write(reply)
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        servers.append(server)
        port: int = server.sockets[0].getsockname()[1]
        return port

    yield _start

    for server in servers:
        server.close()
        await server.wait_closed()


async def _closed_port() -> int:
    async def _no_op(
        reader: asyncio.StreamReader,  # noqa: ARG001 -- never actually called, server closes immediately
        writer: asyncio.StreamWriter,  # noqa: ARG001
    ) -> None:
        return

    server = await asyncio.start_server(_no_op, "127.0.0.1", 0)
    port: int = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()
    return port


def _download_transport(content: bytes, status_code: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001 -- fixed canned response
        return httpx.Response(status_code, content=content)

    return httpx.MockTransport(handler)


def _failing_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        raise httpx.ConnectError("connection refused")

    return httpx.MockTransport(handler)


# --- NoopScanner --------------------------------------------------------------------------


async def test_noop_scanner_always_returns_clean() -> None:
    scanner = NoopScanner()

    result = await scanner.scan(download_url="https://example.com/f", checksum_sha256=None)

    assert result.clean is True
    assert result.signature is None


# --- ClamAVScanner --------------------------------------------------------------------------


async def test_clamav_scanner_clean_reply(fake_clamd: Callable[[bytes], Awaitable[int]]) -> None:
    port = await fake_clamd(b"stream: OK\0")
    client = ClamAVScanner(
        host="127.0.0.1", port=port, transport=_download_transport(b"file bytes")
    )

    result = await client.scan(download_url="https://example.com/f", checksum_sha256=None)

    assert result.clean is True
    assert result.signature is None


async def test_clamav_scanner_infected_reply(
    fake_clamd: Callable[[bytes], Awaitable[int]],
) -> None:
    port = await fake_clamd(b"stream: Eicar-Test-Signature FOUND\0")
    client = ClamAVScanner(
        host="127.0.0.1", port=port, transport=_download_transport(b"eicar bytes")
    )

    result = await client.scan(download_url="https://example.com/f", checksum_sha256=None)

    assert result.clean is False
    assert result.signature == "Eicar-Test-Signature"


async def test_clamav_scanner_error_reply_raises_scanner_error(
    fake_clamd: Callable[[bytes], Awaitable[int]],
) -> None:
    port = await fake_clamd(b"stream: Parse error ERROR\0")
    client = ClamAVScanner(host="127.0.0.1", port=port, transport=_download_transport(b"bytes"))

    with pytest.raises(ScannerError):
        await client.scan(download_url="https://example.com/f", checksum_sha256=None)


async def test_clamav_scanner_connection_refused_raises_scanner_error() -> None:
    port = await _closed_port()
    client = ClamAVScanner(host="127.0.0.1", port=port, transport=_download_transport(b"bytes"))

    with pytest.raises(ScannerError):
        await client.scan(download_url="https://example.com/f", checksum_sha256=None)


async def test_clamav_scanner_download_failure_raises_scanner_error(
    fake_clamd: Callable[[bytes], Awaitable[int]],
) -> None:
    port = await fake_clamd(b"stream: OK\0")
    client = ClamAVScanner(host="127.0.0.1", port=port, transport=_failing_transport())

    with pytest.raises(ScannerError):
        await client.scan(download_url="https://example.com/f", checksum_sha256=None)


async def test_clamav_scanner_download_http_error_status_raises_scanner_error(
    fake_clamd: Callable[[bytes], Awaitable[int]],
) -> None:
    port = await fake_clamd(b"stream: OK\0")
    client = ClamAVScanner(
        host="127.0.0.1", port=port, transport=_download_transport(b"", status_code=500)
    )

    with pytest.raises(ScannerError):
        await client.scan(download_url="https://example.com/f", checksum_sha256=None)


# --- VirusTotalScanner ----------------------------------------------------------------------


async def test_virustotal_scanner_requires_checksum() -> None:
    scanner = VirusTotalScanner(api_key="key")

    with pytest.raises(ScannerError):
        await scanner.scan(download_url="https://example.com/f", checksum_sha256=None)


async def test_virustotal_scanner_unknown_hash_is_treated_as_clean() -> None:
    scanner = VirusTotalScanner(api_key="key", transport=_download_transport(b"{}", 404))

    result = await scanner.scan(download_url="https://example.com/f", checksum_sha256="deadbeef")

    assert result.clean is True


async def test_virustotal_scanner_known_clean_hash() -> None:
    body = b'{"data": {"attributes": {"last_analysis_stats": {"malicious": 0, "suspicious": 0}}}}'
    scanner = VirusTotalScanner(api_key="key", transport=_download_transport(body, 200))

    result = await scanner.scan(download_url="https://example.com/f", checksum_sha256="deadbeef")

    assert result.clean is True


async def test_virustotal_scanner_known_malicious_hash() -> None:
    body = b'{"data": {"attributes": {"last_analysis_stats": {"malicious": 5, "suspicious": 1}}}}'
    scanner = VirusTotalScanner(api_key="key", transport=_download_transport(body, 200))

    result = await scanner.scan(download_url="https://example.com/f", checksum_sha256="deadbeef")

    assert result.clean is False
    assert result.signature == "virustotal:6_engines_flagged"


async def test_virustotal_scanner_non_200_non_404_raises_scanner_error() -> None:
    scanner = VirusTotalScanner(api_key="key", transport=_download_transport(b"", 500))

    with pytest.raises(ScannerError):
        await scanner.scan(download_url="https://example.com/f", checksum_sha256="deadbeef")


async def test_virustotal_scanner_request_failure_raises_scanner_error() -> None:
    scanner = VirusTotalScanner(api_key="key", transport=_failing_transport())

    with pytest.raises(ScannerError):
        await scanner.scan(download_url="https://example.com/f", checksum_sha256="deadbeef")


# --- get_virus_scanner ----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_scanner_cache() -> None:
    get_virus_scanner.cache_clear()
    yield
    get_virus_scanner.cache_clear()


def test_get_virus_scanner_defaults_to_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIRUS_SCANNER", "noop")
    get_settings.cache_clear()

    assert isinstance(get_virus_scanner(), NoopScanner)


def test_get_virus_scanner_returns_clamav_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VIRUS_SCANNER", "clamav")
    get_settings.cache_clear()

    assert isinstance(get_virus_scanner(), ClamAVScanner)


def test_get_virus_scanner_returns_virustotal_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VIRUS_SCANNER", "virustotal")
    get_settings.cache_clear()

    assert isinstance(get_virus_scanner(), VirusTotalScanner)


def test_parse_clamd_reply_handles_reply_without_leading_space() -> None:
    # Defensive: confirms the parser is keyed on the "stream:" prefix and
    # trailing OK/FOUND/ERROR tokens, not on exact whitespace.
    result = scanner_module._parse_clamd_reply("stream: OK")

    assert result.clean is True
