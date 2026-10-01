"""
T-212 (OWASP A10 SSRF): the gate every server-side fetch of a user-supplied
URL goes through. Rejects non-http(s) schemes, private/loopback/link-local/
reserved literal IPs, and DNS rebinding -- a hostname whose text looks public
but resolves to a private address.

Literal IPs skip the DNS step entirely: there's no name to rebind, so the one
`ipaddress` check is both necessary and sufficient. Hostnames always go
through `getaddrinfo` so the *resolved* address is what's judged, not the
string.

**Validating is only half of it.** This module used to return the URL string
and leave the caller to hand it to httpx, which resolved the hostname a
second time -- so an attacker controlling the DNS answer could return a
public address to the check and a private one to the connection, a few
milliseconds apart. Checking again at delivery time does not close that: any
check that ends before the socket opens can be raced by the next lookup.

So `validate_outbound_url` now hands back a `SafeDestination` carrying the
address it actually approved, and `pinned_transport` builds an httpx
transport that connects to *that address* and refuses anything else. The
hostname is still used for the `Host` header and the TLS SNI/certificate
check, so pinning weakens neither: a certificate is still verified against
the name, and a rebind can no longer change where the bytes go.
"""

from __future__ import annotations

import asyncio
import ipaddress
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpcore
import httpx

_ALLOWED_SCHEMES = frozenset({"http", "https"})

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class UnsafeOutboundURLError(ValueError):
    """Raised when a URL is not safe to fetch from this server."""


@dataclass(frozen=True)
class SafeDestination:
    """A URL that passed the guard, and the address it passed *as*.

    `address` is the point: it is the resolution the check approved, and the
    only one `pinned_transport` will connect to.
    """

    url: str
    hostname: str
    address: str


def _is_private_or_reserved(ip: IPAddress) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


async def validate_outbound_url(url: str, *, require_https: bool = False) -> SafeDestination:
    """Approve `url` for this server to fetch, or raise.

    `require_https` for any destination that will carry a credential -- an
    OIDC token exchange sends the client secret in the request body, and
    plaintext http would hand it to anyone on the path.

    DNS runs on the event loop's resolver rather than a blocking
    `socket.getaddrinfo`, which used to stall every other coroutine in the
    worker for the duration of a slow lookup (repository audit finding 9).
    """
    parts = urlsplit(url)
    if parts.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeOutboundURLError(f"scheme {parts.scheme!r} is not allowed")
    if require_https and parts.scheme != "https":
        raise UnsafeOutboundURLError(
            f"scheme {parts.scheme!r} is not allowed for a credential-bearing request"
        )

    hostname = parts.hostname
    if not hostname:
        raise UnsafeOutboundURLError("URL has no hostname")

    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        if _is_private_or_reserved(literal_ip):
            raise UnsafeOutboundURLError(f"{hostname} is a private/reserved address")
        return SafeDestination(url=url, hostname=hostname, address=hostname)

    return SafeDestination(
        url=url, hostname=hostname, address=await _resolve_public_address(hostname)
    )


async def _getaddrinfo(hostname: str) -> list[Any]:
    """Name resolution, as one patchable seam.

    A function rather than an inline `loop.getaddrinfo` so a test can supply
    fake DNS *here* instead of patching `socket.getaddrinfo` globally. That
    distinction is load-bearing for any test holding a real database
    connection: a global patch also answers asyncpg's own lookup, with a
    fabricated `(ip, 0)` sockaddr, and port zero fails the connection before
    the first assertion runs.

    On the loop's resolver rather than the blocking call this module used to
    make directly -- a slow lookup stalled every other coroutine in the
    worker (repository audit finding 9).
    """
    loop = asyncio.get_running_loop()
    return list(await loop.getaddrinfo(hostname, None))


async def _resolve_public_address(hostname: str) -> str:
    """The first address `hostname` resolves to, once every answer is clean.

    Every answer has to pass, not merely the one we would use: a resolver
    handing back one public and one private address is exactly the shape of a
    rebinding attack, and which one a connection would pick is not ours to
    predict.
    """
    try:
        resolved = await _getaddrinfo(hostname)
    except OSError as exc:
        raise UnsafeOutboundURLError(f"could not resolve host {hostname!r}") from exc

    approved: str | None = None
    for *_, sockaddr in resolved:
        resolved_ip = ipaddress.ip_address(sockaddr[0])
        if _is_private_or_reserved(resolved_ip):
            raise UnsafeOutboundURLError(
                f"{hostname} resolves to private/reserved address {resolved_ip}"
            )
        if approved is None:
            approved = str(resolved_ip)

    if approved is None:
        raise UnsafeOutboundURLError(f"could not resolve host {hostname!r}")
    return approved


class _PinnedBackend(httpcore.AsyncNetworkBackend):
    """Connects to one approved address, and refuses every other host.

    httpcore asks its backend to open a socket to the origin's host; this
    substitutes the address the guard approved, so no second DNS lookup can
    disagree with the first. TLS is untouched -- httpcore calls `start_tls`
    with the origin's *hostname*, so SNI and certificate verification still
    happen against the name.

    The refusal is not defence in depth for its own sake. Redirects are
    disabled at every call site, but that is a flag somebody could flip; a
    redirect to a new host would reach this backend, and this is where it
    stops.
    """

    def __init__(self, hostname: str, address: str) -> None:
        self._hostname = hostname
        self._address = address
        self._inner = httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 -- httpcore's own backend signature
        local_address: str | None = None,
        socket_options: Any = None,  # noqa: ANN401 -- httpcore's own signature
    ) -> httpcore.AsyncNetworkStream:
        if host != self._hostname:
            raise httpcore.ConnectError(
                f"refusing to connect to {host!r}: only {self._hostname!r} was validated"
            )
        return await self._inner.connect_tcp(
            self._address,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


def pinned_transport(
    destination: SafeDestination,
    **transport_kwargs: Any,  # noqa: ANN401 -- forwarded straight to httpx
) -> httpx.AsyncHTTPTransport:
    """An httpx transport that can only reach `destination`'s approved address."""
    transport = httpx.AsyncHTTPTransport(**transport_kwargs)
    pool = transport._pool  # noqa: SLF001 -- see below
    # httpx builds its `AsyncConnectionPool` internally and does not accept a
    # `network_backend`, though httpcore does. Reconstructing the pool here
    # would mean duplicating httpx's SSL-context and limits wiring, which is
    # a worse thing to get subtly wrong than one private attribute.
    #
    # The guard is that a rename must *fail*, never silently un-pin: an
    # unpinned transport looks identical and re-resolves the hostname, which
    # is the whole vulnerability back again.
    if not hasattr(pool, "_network_backend"):
        raise RuntimeError(
            "httpcore.AsyncConnectionPool has no _network_backend: this httpx/httpcore "
            "version cannot be pinned, and an unpinned transport would silently reopen "
            "the DNS-rebinding hole this exists to close."
        )
    pool._network_backend = _PinnedBackend(destination.hostname, destination.address)  # noqa: SLF001
    return transport
