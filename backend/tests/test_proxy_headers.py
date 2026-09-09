"""Which X-Forwarded-For entry the app believes.

`request.client.host` is the rate-limit key for every unauthenticated request
(app.middleware.rate_limit._key_func) and the `ip` on every audit event, so
"which entry wins" is a security decision, not a formatting one.

Proxies *append* to X-Forwarded-For rather than replacing it: Caddy's
reverse_proxy does, and Render's router does not reset a client-supplied
value. A forged header therefore arrives as `<forged>, <real client>`, and
only the rightmost untrusted entry is trustworthy. uvicorn's
ProxyHeadersMiddleware implements exactly that -- but *only* when it is given
a real list of trusted addresses. Configured with "*" it short-circuits to
the leftmost entry instead, which is the forged one.

These tests drive the middleware directly rather than through TestClient,
because what is being pinned is the choice of entry given a peer address, and
TestClient's peer is always "testclient".
"""

import pytest
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

PRIVATE_RANGES = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]

# What a forged request looks like once the proxy has appended to it: the
# caller's invention first, the address the proxy actually observed last.
FORGED_THEN_REAL = b"9.9.9.9, 203.0.113.50"


async def _client_seen_by_app(trusted: list[str] | str, forwarded_for: bytes, peer: str) -> str:
    seen: dict[str, str] = {}

    async def app(scope: dict, _receive: object, _send: object) -> None:
        seen["host"] = scope["client"][0]

    middleware = ProxyHeadersMiddleware(app, trusted_hosts=trusted)  # type: ignore[arg-type]
    await middleware(
        {
            "type": "http",
            "client": (peer, 51234),
            "headers": [(b"x-forwarded-for", forwarded_for)],
        },
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
    )
    return seen["host"]


@pytest.mark.owasp("A07")
@pytest.mark.asyncio
async def test_forged_forwarded_for_is_ignored_with_real_trusted_ranges() -> None:
    """The shipped configuration: the proxy's observation wins, not the caller's."""
    host = await _client_seen_by_app(PRIVATE_RANGES, FORGED_THEN_REAL, peer="10.1.2.3")

    assert host == "203.0.113.50"


@pytest.mark.owasp("A07")
@pytest.mark.asyncio
async def test_wildcard_trust_hands_the_caller_the_wheel() -> None:
    """Why TRUSTED_PROXY_IPS must not be "*", pinned so nobody "simplifies" it.

    With every host trusted, uvicorn cannot tell which entries came from a
    proxy, so it returns the leftmost -- the one the caller wrote. Each
    request can then claim a different address: a fresh rate-limit bucket per
    attempt, and an arbitrary IP on every audit event.
    """
    host = await _client_seen_by_app("*", FORGED_THEN_REAL, peer="10.1.2.3")

    assert host == "9.9.9.9"


@pytest.mark.asyncio
async def test_untrusted_peer_is_not_allowed_to_speak_for_anyone() -> None:
    """A caller reaching the port directly cannot set its own address."""
    host = await _client_seen_by_app(PRIVATE_RANGES, FORGED_THEN_REAL, peer="203.0.113.99")

    assert host == "203.0.113.99"
