"""
The IdP's discovery document and its signing keys (T-331), cached in Redis.

Two fetches stand between a visitor and a session: `/.well-known/openid-
configuration` for the endpoint URLs, and the `jwks_uri` behind it for the
keys that verify the ID token. Doing both on every login would make sign-in
three round trips instead of one and would hand the IdP a way to rate-limit
this product's front door, so both are cached under
`DISCOVERY_CACHE_TTL_SECONDS`.

**The keys are cached, so a key rotation has to be recoverable without
waiting.** `fetch_jwks(force=True)` is what the validator calls once, after a
signature fails against the cached set — an IdP that rotated five minutes ago
would otherwise lock every user out for the rest of the TTL. That single
retry is bounded on purpose: an attacker who can make signature verification
fail can make this product fetch the JWKS, so it must not be a fetch *per
attempt*, and it must not be a fetch against an attacker-chosen URL —
`jwks_uri` comes from the discovery document, which comes from the
connection's stored `discovery_url`, which an org admin set.

Both URLs go through T-212's `validate_outbound_url` before they are
fetched. A discovery URL is operator-supplied, which is exactly the input
class that guard exists for: without it, "connect an IdP" is an SSRF
primitive pointed at `169.254.169.254` by anyone who can edit an
organization's settings.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx
from loguru import logger

from app.core import cache_keys
from app.core.exceptions import ExternalServiceUnavailableError, UnprocessableEntityError
from app.core.url_guard import (
    UnsafeOutboundURLError,
    pinned_transport,
    validate_outbound_url,
)

from .models import DISCOVERY_CACHE_TTL_SECONDS

if TYPE_CHECKING:
    from redis.asyncio import Redis

# An IdP that is up but slow must not hold a login request open. Ten seconds
# is the same budget T-321's delivery job gives a webhook consumer, and for
# the same reason.
_TIMEOUT_SECONDS = 10.0

# Fields this product actually uses. A discovery document missing any of them
# is a misconfiguration to report at *save* time, not a KeyError at login.
REQUIRED_DISCOVERY_FIELDS = ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri")


class DiscoveryError(UnprocessableEntityError):
    """The discovery document is absent, unreadable, or not an OIDC one."""


def new_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """The one place this module builds an HTTP client.

    A named function rather than an inline constructor purely so a test can
    install a `MockTransport` *here*. Patching `httpx.AsyncClient` itself is
    the obvious alternative and is a trap: the test suite's own client is an
    `httpx.AsyncClient` wrapping an `ASGITransport`, so a global patch sends
    every request the test makes to the mock IdP instead of to the app, and
    the symptom is an empty 404 from a route that plainly exists.

    `follow_redirects=False` because a redirect is a second destination the
    outbound guard never judged -- the same reason T-321's delivery job
    refuses them.
    """
    return httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, follow_redirects=False, transport=transport)


async def fetch_discovery(
    redis: Redis, discovery_url: str, *, force: bool = False
) -> dict[str, Any]:
    """The IdP's discovery document, from cache unless `force`."""
    key = cache_keys.sso_discovery_key(discovery_url)
    if not force:
        cached = await _cached(redis, key)
        if cached is not None:
            return cached
    document = await _get_json(discovery_url, what="discovery document")
    missing = [field for field in REQUIRED_DISCOVERY_FIELDS if not document.get(field)]
    if missing:
        raise DiscoveryError(
            "errors.sso_discovery_incomplete",
            fallback=f"The discovery document is missing: {', '.join(missing)}",
            params={"fields": ", ".join(missing)},
        )
    await _store(redis, key, document)
    return document


async def fetch_jwks(
    redis: Redis, discovery: dict[str, Any], *, force: bool = False
) -> dict[str, Any]:
    """The IdP's signing keys. `force` re-fetches past the cache — see the
    module docstring for why that is exactly one retry and not a policy."""
    jwks_uri = str(discovery["jwks_uri"])
    key = cache_keys.sso_jwks_key(jwks_uri)
    if not force:
        cached = await _cached(redis, key)
        if cached is not None:
            return cached
    keys = await _get_json(jwks_uri, what="JWKS")
    if not isinstance(keys.get("keys"), list) or not keys["keys"]:
        raise DiscoveryError(
            "errors.sso_jwks_empty", fallback="The identity provider published no signing keys"
        )
    await _store(redis, key, keys)
    return keys


async def invalidate(redis: Redis, discovery_url: str, jwks_uri: str | None) -> None:
    """Drops both cached documents for one connection.

    Called on every connection edit, so a corrected discovery URL takes
    effect on the next login rather than at the end of the hour -- which is
    the shape of "I fixed it but it is still broken" that costs an evening.
    """
    await redis.delete(cache_keys.sso_discovery_key(discovery_url))
    if jwks_uri:
        await redis.delete(cache_keys.sso_jwks_key(jwks_uri))


async def _cached(redis: Redis, key: str) -> dict[str, Any] | None:
    raw = await redis.get(key)
    if raw is None:
        return None
    try:
        value: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError:  # pragma: no cover -- only a hand-edited key
        return None
    return value


async def _store(redis: Redis, key: str, document: dict[str, Any]) -> None:
    await redis.set(key, json.dumps(document), ex=DISCOVERY_CACHE_TTL_SECONDS)


async def _get_json(url: str, *, what: str) -> dict[str, Any]:
    try:
        destination = await validate_outbound_url(url)
    except UnsafeOutboundURLError as exc:
        raise DiscoveryError(
            "errors.sso_unsafe_url",
            fallback=f"This {what} URL cannot be fetched: {exc}",
            params={"reason": str(exc)},
        ) from exc

    try:
        # Pinned to the address the check approved. Without this the client
        # resolves the hostname again and an attacker-controlled answer can
        # differ from the one that was judged safe moments earlier.
        async with new_client(pinned_transport(destination)) as client:
            response = await client.get(destination.url, headers={"accept": "application/json"})
            response.raise_for_status()
            document: dict[str, Any] = response.json()
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("sso.fetch_failed: what={} url={} error={}", what, url, exc)
        # 503, not 422: the configuration may be perfectly fine and the IdP
        # merely unreachable, and telling an admin their settings are wrong
        # when they are not is how a five-minute outage becomes an afternoon.
        # The error names the degraded *feature*, never the IdP -- that is
        # ExternalServiceUnavailableError's own contract.
        raise ExternalServiceUnavailableError("Single sign-on") from exc
    if not isinstance(document, dict):
        raise DiscoveryError(
            "errors.sso_discovery_invalid",
            fallback=f"The identity provider's {what} was not a JSON object",
        )
    return document
