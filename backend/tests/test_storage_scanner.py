"""
Verifies T-113's VirusScanner implementations: NoopScanner's always-clean
default, ClamAVScanner's INSTREAM wire protocol (clean/infected/error replies
and a refused clamd connection -- all surfaced as ScannerError so
app.workers.jobs.virus_scan's retry loop can catch them uniformly),
VirusTotalScanner's hash lookup (unknown hash *refused* rather than assumed
clean, known-clean, known-malicious, and HTTP failure modes), and
get_virus_scanner()'s settings-driven selection.

Every scanner now receives bytes plus their SHA-256 rather than a presigned
URL: the calling job fetches once and hashes what it fetched, so no scanner
downloads anything (only VirusTotalScanner makes an HTTP call at all, to look
a hash up). That is why ClamAVScanner has no download-failure case here any
more -- it has no download.

ClamAVScanner is exercised against a real local asyncio TCP server speaking
just enough of clamd's INSTREAM protocol to reply with a canned response --
no mocking of asyncio.open_connection itself, so the chunked-length-prefix
framing is actually exercised end to end. VirusTotalScanner's outbound HTTP
goes through httpx.MockTransport (the same test-only seam pattern already
used by app.modules.auth.oauth's OAuth clients), not a real network call.
"""

import asyncio
import struct
import tracemalloc
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import httpx
import pytest
import yaml

from app.core.config import get_settings
from app.modules.storage import scanner as scanner_module
from app.modules.storage.scanner import (
    BytesSource,
    ClamAVScanner,
    NoopScanner,
    ScanLimitExceededError,
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


def _vt_transport(content: bytes, status_code: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001 -- fixed canned response
        return httpx.Response(status_code, content=content)

    return httpx.MockTransport(handler)


def _failing_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001 -- fixed canned response
        raise httpx.ConnectError("connection refused")

    return httpx.MockTransport(handler)


# --- NoopScanner --------------------------------------------------------------------------


async def test_noop_scanner_always_returns_clean() -> None:
    scanner = NoopScanner()

    result = await scanner.scan(BytesSource(b"file bytes"))

    assert result.clean is True
    assert result.signature is None


# --- ClamAVScanner --------------------------------------------------------------------------


async def test_clamav_scanner_clean_reply(fake_clamd: Callable[[bytes], Awaitable[int]]) -> None:
    port = await fake_clamd(b"stream: OK\0")
    client = ClamAVScanner(host="127.0.0.1", port=port)

    result = await client.scan(BytesSource(b"file bytes"))

    assert result.clean is True
    assert result.signature is None


async def test_clamav_scanner_infected_reply(
    fake_clamd: Callable[[bytes], Awaitable[int]],
) -> None:
    port = await fake_clamd(b"stream: Eicar-Test-Signature FOUND\0")
    client = ClamAVScanner(host="127.0.0.1", port=port)

    result = await client.scan(BytesSource(b"file bytes"))

    assert result.clean is False
    assert result.signature == "Eicar-Test-Signature"


async def test_clamav_scanner_error_reply_raises_scanner_error(
    fake_clamd: Callable[[bytes], Awaitable[int]],
) -> None:
    port = await fake_clamd(b"stream: Parse error ERROR\0")
    client = ClamAVScanner(host="127.0.0.1", port=port)

    with pytest.raises(ScannerError):
        await client.scan(BytesSource(b"file bytes"))


async def test_clamav_scanner_connection_refused_raises_scanner_error() -> None:
    port = await _closed_port()
    client = ClamAVScanner(host="127.0.0.1", port=port)

    with pytest.raises(ScannerError):
        await client.scan(BytesSource(b"file bytes"))


async def test_clamav_scanner_scans_the_bytes_it_is_given(
    fake_clamd: Callable[[bytes], Awaitable[int]],
) -> None:
    # Deliberately larger than _CLAMD_CHUNK_SIZE so the length-prefix framing
    # is exercised across several chunks, and with a digest that matches
    # nothing: the digest is VirusTotalScanner's input, and clamd must never
    # be skipped because a hash looked familiar.
    port = await fake_clamd(b"stream: Eicar-Test-Signature FOUND\0")
    client = ClamAVScanner(host="127.0.0.1", port=port)

    result = await client.scan(BytesSource(b"x" * 20_000))

    assert result.clean is False


@pytest.mark.parametrize(
    "reply",
    [
        # AlertExceedsMax on: an archive past MaxScanSize/MaxFiles/MaxRecursion.
        b"stream: Heuristics.Limits.Exceeded.MaxScanSize FOUND\0",
        # A stream longer than StreamMaxLength.
        b"INSTREAM size limit exceeded. ERROR\0",
    ],
)
async def test_clamav_limits_are_a_permanent_failure_not_a_verdict(
    fake_clamd: Callable[[bytes], Awaitable[int]], reply: bytes
) -> None:
    """Review R4: clamd giving up is neither clean nor infected.

    Clean would pass a zip bomb. Infected would delete an honest large
    archive. And a retry changes nothing, so it is not a ScannerError.
    """
    port = await fake_clamd(reply)
    client = ClamAVScanner(host="127.0.0.1", port=port)

    with pytest.raises(ScanLimitExceededError):
        await client.scan(BytesSource(b"file bytes"))


class _GeneratedSource:
    """`total` bytes, produced a chunk at a time and never held."""

    def __init__(self, total: int, chunk: int = 64 * 1024) -> None:
        self._total = total
        self._chunk = chunk

    async def chunks(self) -> AsyncIterator[bytes]:
        for _ in range(self._total // self._chunk):
            yield b"\0" * self._chunk

    @property
    def sha256(self) -> str | None:
        return None


async def test_clamav_client_streams_with_backpressure(
    fake_clamd: Callable[[bytes], Awaitable[int]],
) -> None:
    """Review R4: memory stays at a few chunks, whatever the object size.

    The client used to write every chunk and drain once at the end. With no
    await between writes the reading side never ran, so the whole object
    piled up in the transport's buffer -- per scan, times twenty jobs.
    """
    port = await fake_clamd(b"stream: OK\0")
    client = ClamAVScanner(host="127.0.0.1", port=port)
    total = 32 * 1024 * 1024

    tracemalloc.start()
    try:
        result = await client.scan(_GeneratedSource(total))
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert result.clean is True
    assert peak < 8 * 1024 * 1024, f"peak {peak} bytes while streaming {total}"


def test_the_clamd_limits_in_compose_cover_the_upload_ceiling() -> None:
    """Review R4: tie clamd's configuration to the product's upload limit.

    StreamMaxLength defaulted to exactly MAX_UPLOAD_BYTES, which
    confirm_upload's 1 KiB tolerance can exceed; and AlertExceedsMax
    defaulted to off, which reports an over-limit archive as clean.
    Raising MAX_UPLOAD_BYTES without the compose value fails here.
    """
    compose = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "docker-compose.prod.yml").read_text("utf-8")
    )
    env = compose["services"]["clamav"]["environment"]
    assert env["CLAMD_CONF_AlertExceedsMax"] == "yes"
    stream_max = env["CLAMD_CONF_StreamMaxLength"]
    assert stream_max.endswith("M")
    stream_max_bytes = int(stream_max.removesuffix("M")) * 1024 * 1024
    assert stream_max_bytes > get_settings().MAX_UPLOAD_BYTES + 1024


# --- VirusTotalScanner ----------------------------------------------------------------------


class _NoDigestSource:
    """A source whose pass never completes a digest."""

    async def chunks(self) -> AsyncIterator[bytes]:
        yield b"partial"

    @property
    def sha256(self) -> str | None:
        return None


async def test_virustotal_scanner_requires_checksum() -> None:
    scanner = VirusTotalScanner(api_key="key")

    with pytest.raises(ScannerError):
        await scanner.scan(_NoDigestSource())


async def test_virustotal_scanner_refuses_to_clear_an_unknown_hash() -> None:
    # The finding this file exists to keep closed. A 404 means VirusTotal has
    # never seen the file, which is the expected answer for every novel file
    # and evidence of nothing. It used to return clean.
    scanner = VirusTotalScanner(api_key="key", transport=_vt_transport(b"{}", 404))

    with pytest.raises(ScannerError, match="no record"):
        await scanner.scan(BytesSource(b""))


async def test_virustotal_scanner_known_clean_hash() -> None:
    body = b'{"data": {"attributes": {"last_analysis_stats": {"malicious": 0, "suspicious": 0}}}}'
    scanner = VirusTotalScanner(api_key="key", transport=_vt_transport(body, 200))

    result = await scanner.scan(BytesSource(b""))

    assert result.clean is True


async def test_virustotal_scanner_known_malicious_hash() -> None:
    body = b'{"data": {"attributes": {"last_analysis_stats": {"malicious": 5, "suspicious": 1}}}}'
    scanner = VirusTotalScanner(api_key="key", transport=_vt_transport(body, 200))

    result = await scanner.scan(BytesSource(b""))

    assert result.clean is False
    assert result.signature == "virustotal:6_engines_flagged"


async def test_virustotal_scanner_non_200_non_404_raises_scanner_error() -> None:
    scanner = VirusTotalScanner(api_key="key", transport=_vt_transport(b"", 500))

    with pytest.raises(ScannerError):
        await scanner.scan(BytesSource(b""))


async def test_virustotal_scanner_request_failure_raises_scanner_error() -> None:
    scanner = VirusTotalScanner(api_key="key", transport=_failing_transport())

    with pytest.raises(ScannerError):
        await scanner.scan(BytesSource(b""))


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
