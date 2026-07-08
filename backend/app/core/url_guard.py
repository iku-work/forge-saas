"""
T-212 (OWASP A10 SSRF): the one gate outbound-webhook code must call before
dispatching any user-supplied URL server-side (Phase 4, T-32x, will be the
first real caller). Rejects non-http(s) schemes, private/loopback/link-local/
reserved literal IPs, and DNS rebinding -- a hostname whose text looks public
but resolves to a private address.

Literal IPs skip the DNS step entirely: there's no name to rebind, so the one
`ipaddress` check is both necessary and sufficient. Hostnames always go
through `socket.getaddrinfo` so the *resolved* address is what's judged, not
the string.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

_ALLOWED_SCHEMES = frozenset({"http", "https"})

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class UnsafeOutboundURLError(ValueError):
    """Raised when a URL is not safe to fetch from this server."""


def _is_private_or_reserved(ip: IPAddress) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_outbound_url(url: str) -> str:
    """Return `url` unchanged if safe for this server to fetch; raise otherwise."""
    parts = urlsplit(url)
    if parts.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeOutboundURLError(f"scheme {parts.scheme!r} is not allowed")

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
        return url

    try:
        resolved = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise UnsafeOutboundURLError(f"could not resolve host {hostname!r}") from exc

    for _family, _type, _proto, _canonname, sockaddr in resolved:
        resolved_ip = ipaddress.ip_address(sockaddr[0])
        if _is_private_or_reserved(resolved_ip):
            raise UnsafeOutboundURLError(
                f"{hostname} resolves to private/reserved address {resolved_ip}"
            )

    return url
