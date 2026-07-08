"""
T-212 (OWASP A10 SSRF): `socket.getaddrinfo` is monkeypatched to a fixed
fake-DNS table rather than hitting real DNS -- deterministic regardless of
network access, and lets the "DNS rebinding" case (a hostname whose *text*
looks public but *resolves* to a private address) be expressed at all.
Literal-IP inputs never call `getaddrinfo` (see url_guard.py's docstring), so
those cases are unaffected by the monkeypatch.
"""

import socket

import pytest

from app.core.url_guard import UnsafeOutboundURLError, validate_outbound_url

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


def test_localhost_hostname_resolves_to_loopback_and_is_rejected() -> None:
    with pytest.raises(UnsafeOutboundURLError):
        validate_outbound_url("http://localhost")


def test_literal_private_ip_is_rejected() -> None:
    with pytest.raises(UnsafeOutboundURLError):
        validate_outbound_url("http://10.0.0.1")


def test_literal_link_local_metadata_ip_is_rejected() -> None:
    with pytest.raises(UnsafeOutboundURLError):
        validate_outbound_url("http://169.254.169.254/latest/meta-data")


def test_file_scheme_is_rejected() -> None:
    with pytest.raises(UnsafeOutboundURLError):
        validate_outbound_url("file:///etc/passwd")


def test_hostname_that_dns_rebinds_to_a_private_ip_is_rejected() -> None:
    with pytest.raises(UnsafeOutboundURLError):
        validate_outbound_url("http://rebinding.internal.test/webhook")


def test_unresolvable_hostname_is_rejected() -> None:
    with pytest.raises(UnsafeOutboundURLError):
        validate_outbound_url("http://this-domain-does-not-exist.invalid")


def test_url_with_no_hostname_is_rejected() -> None:
    with pytest.raises(UnsafeOutboundURLError):
        validate_outbound_url("http://")


def test_literal_public_ip_is_accepted() -> None:
    assert validate_outbound_url("http://93.184.216.34") == "http://93.184.216.34"


def test_https_example_com_is_accepted() -> None:
    assert validate_outbound_url("https://example.com") == "https://example.com"
