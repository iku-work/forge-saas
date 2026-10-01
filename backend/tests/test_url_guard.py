"""
T-212 (OWASP A10 SSRF): `socket.getaddrinfo` is monkeypatched to a fixed
fake-DNS table rather than hitting real DNS -- deterministic regardless of
network access, and lets the "DNS rebinding" case (a hostname whose *text*
looks public but *resolves* to a private address) be expressed at all.
Literal-IP inputs never call `getaddrinfo` (see url_guard.py's docstring), so
those cases are unaffected by the monkeypatch.
"""

import socket

import httpcore
import httpx
import pytest

from app.core.url_guard import (
    UnsafeOutboundURLError,
    pinned_transport,
    validate_outbound_url,
)

pytestmark = pytest.mark.owasp("A10")

_FAKE_DNS: dict[str, str] = {
    "localhost": "127.0.0.1",
    "example.com": "93.184.216.34",
    "rebinding.internal.test": "10.0.0.5",
}


def _fake_getaddrinfo(host: str, *_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
    if host not in _FAKE_DNS:
        raise socket.gaierror(socket.EAI_NONAME, "fake DNS: no such host")
    ip = _FAKE_DNS[host]
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


@pytest.fixture(autouse=True)
def _fake_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)


async def test_localhost_hostname_resolves_to_loopback_and_is_rejected() -> None:
    with pytest.raises(UnsafeOutboundURLError):
        await validate_outbound_url("http://localhost")


async def test_literal_private_ip_is_rejected() -> None:
    with pytest.raises(UnsafeOutboundURLError):
        await validate_outbound_url("http://10.0.0.1")


async def test_literal_link_local_metadata_ip_is_rejected() -> None:
    with pytest.raises(UnsafeOutboundURLError):
        await validate_outbound_url("http://169.254.169.254/latest/meta-data")


async def test_file_scheme_is_rejected() -> None:
    with pytest.raises(UnsafeOutboundURLError):
        await validate_outbound_url("file:///etc/passwd")


async def test_hostname_that_dns_rebinds_to_a_private_ip_is_rejected() -> None:
    with pytest.raises(UnsafeOutboundURLError):
        await validate_outbound_url("http://rebinding.internal.test/webhook")


async def test_unresolvable_hostname_is_rejected() -> None:
    with pytest.raises(UnsafeOutboundURLError):
        await validate_outbound_url("http://this-domain-does-not-exist.invalid")


async def test_url_with_no_hostname_is_rejected() -> None:
    with pytest.raises(UnsafeOutboundURLError):
        await validate_outbound_url("http://")


async def test_literal_public_ip_is_accepted() -> None:
    destination = await validate_outbound_url("http://93.184.216.34")

    assert destination.url == "http://93.184.216.34"
    # A literal needs no lookup, so the address it was approved as is itself.
    assert destination.address == "93.184.216.34"


async def test_https_example_com_is_accepted() -> None:
    destination = await validate_outbound_url("https://example.com")

    assert destination.url == "https://example.com"
    assert destination.hostname == "example.com"
    # The resolution that was judged, carried out so the connection can be
    # pinned to it -- this is the whole difference from returning a string.
    assert destination.address == "93.184.216.34"


# --- the half that validating alone never gave us -------------------------------
#
# Approving a URL and then handing the string to httpx means the hostname is
# resolved a *second* time, by the client, from whatever DNS says at that
# moment. An attacker controlling the answer returns a public address to the
# check and a private one to the connection. No amount of re-checking closes
# it: any check that finishes before the socket opens can be raced by the
# next lookup.


class _RecordingBackend(httpcore.AsyncNetworkBackend):
    """Stands in for the real network so a connection attempt is observable."""

    def __init__(self) -> None:
        self.attempted: list[tuple[str, int]] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109, ARG002 -- httpcore's signature
        local_address: str | None = None,  # noqa: ARG002
        socket_options: object = None,  # noqa: ARG002
    ) -> httpcore.AsyncNetworkStream:
        self.attempted.append((host, port))
        raise httpcore.ConnectError("recorded, not connected")


async def test_the_transport_connects_to_the_address_that_was_validated() -> None:
    destination = await validate_outbound_url("https://example.com/hook")
    transport = pinned_transport(destination)
    recorder = _RecordingBackend()
    # Replace only the *inner* backend, so the pinning logic under test still
    # runs and we see what it asked for.
    transport._pool._network_backend._inner = recorder

    with pytest.raises(httpx.ConnectError):
        async with httpx.AsyncClient(transport=transport) as client:
            await client.get("https://example.com/hook")

    # The IP, not the hostname: no second lookup happened, so no second
    # answer could differ from the first.
    assert recorder.attempted == [("93.184.216.34", 443)]


async def test_the_transport_refuses_a_host_it_never_validated() -> None:
    # Redirects are disabled at every call site, but that is a flag somebody
    # could flip. A redirect to a new host arrives here, and stops here.
    destination = await validate_outbound_url("https://example.com/hook")
    transport = pinned_transport(destination)
    recorder = _RecordingBackend()
    transport._pool._network_backend._inner = recorder

    with pytest.raises(httpx.ConnectError):
        async with httpx.AsyncClient(transport=transport) as client:
            await client.get("https://elsewhere.example/hook")

    assert recorder.attempted == []


async def test_pinning_fails_loudly_if_httpx_moves_the_backend_attribute() -> None:
    """An unpinned transport looks identical and reopens the hole silently.

    If a future httpx renames `_network_backend`, this must be a crash at
    construction rather than a transport that quietly resolves DNS again.
    """
    destination = await validate_outbound_url("https://example.com")

    assert hasattr(pinned_transport(destination)._pool, "_network_backend")


async def test_a_credential_bearing_request_refuses_plaintext_http() -> None:
    # The OIDC token exchange posts the client secret in the request body.
    with pytest.raises(UnsafeOutboundURLError, match="credential-bearing"):
        await validate_outbound_url("http://example.com/token", require_https=True)


async def test_https_is_still_accepted_for_a_credential_bearing_request() -> None:
    destination = await validate_outbound_url("https://example.com/token", require_https=True)

    assert destination.address == "93.184.216.34"


async def test_one_private_answer_among_several_rejects_the_whole_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A split answer is the rebinding shape, not a choice to make.

    Which address a connection would have picked is not ours to predict, so
    a name resolving to both a public and a private address is refused
    outright rather than pinned to the public one.
    """

    def _split(host: str, *_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
        assert host == "split.example"
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", _split)

    with pytest.raises(UnsafeOutboundURLError, match=r"10\.0\.0\.5"):
        await validate_outbound_url("https://split.example")
