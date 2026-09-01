"""
The OIDC protocol itself (T-331): build an authorization URL, trade the code
for tokens, and decide whether the ID token that comes back is real.

Everything security-relevant about this feature is in `verify_id_token`.
T-331 names four rejections and they are four separate checks here, each
with its own error, because "SSO login failed" is a support ticket and
"`aud` did not match the configured client id" is a fix:

  * **signature** against the IdP's published JWKS, with the algorithm
    constrained to an allowlist. `joserfc` refuses `alg: none` on its own --
    it will not even *encode* one -- but the allowlist is what stops
    algorithm confusion, where a token signed with HS256 using the RSA public
    key as the MAC secret verifies against a validator that trusts the
    header.
  * **`iss`** against the connection's stored issuer, not against the
    discovery document's. The two must agree (`SsoConnectionService` checks
    that when the connection is saved); comparing against the *stored* value
    is what makes a swapped discovery URL a failure rather than a silent
    redirect to somebody else's IdP.
  * **`aud`** against the configured client id, so a token minted for a
    different relying party at the same IdP is not a session here. This is
    the check that stops one tenant of a shared IdP from replaying another's
    token.
  * **`exp`** (and `iat`, with the same bounded skew), so an old token is not
    a key.

And a fifth that OIDC requires and the task lists separately: **`nonce`**,
matched against the value this product generated and signed into the `state`
it sent. That is what makes the token specific to *this* login attempt and
not one captured from another.

`amr`/`acr` are read but never trusted to *lower* a bar -- see
`asserted_mfa`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import httpx
from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.httpx_client import AsyncOAuth2Client
from authlib.oauth2 import OAuth2Error
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

from app.core.exceptions import AuthenticationError

from . import discovery as discovery_module

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from .models import SsoConnection

# Asymmetric only. A symmetric alg in an OIDC ID token means the MAC key is
# the client secret, which is a legitimate (if rare) configuration -- and
# accepting it here alongside RS/ES would reintroduce exactly the algorithm
# confusion the allowlist exists to prevent, because the validator would have
# to pick a key by looking at the header. A product whose IdP only offers
# HS256 should say so in a review, not get it by default.
ALLOWED_ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256")

# Tolerance for clock drift between this server and the IdP, in seconds, in
# both directions. Sixty seconds is the same order as the five minutes
# T-320's webhook signatures allow, tightened because an ID token is
# single-use and short-lived by design.
CLOCK_SKEW_SECONDS = 60

# `acr` values that mean "a second factor was checked", from the two IdPs a
# product is most likely to meet plus the two standard ones. An unrecognised
# value is not MFA -- see `_asserted_mfa` for why that direction is the safe
# one.
_MFA_ACR_VALUES = frozenset(
    {
        "http://schemas.microsoft.com/claims/multipleauthn",
        "urn:okta:loa:2fa:any",
        "phr",
        "phrh",
    }
)

_TOKEN_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class SsoIdentity:
    """What an IdP asserted, once this module believes it.

    Deliberately small. Everything a *product* might want about a person
    lives in this product's own tables; what an IdP is authoritative for is
    who they are and, sometimes, which groups they are in.
    """

    subject: str
    email: str
    full_name: str | None
    groups: tuple[str, ...]
    asserted_mfa: bool


def authorization_url(
    discovery: dict[str, Any],
    connection: SsoConnection,
    *,
    redirect_uri: str,
    state: str,
    nonce: str,
) -> str:
    """Where to send the browser. `state` carries this product's CSRF
    defence, `nonce` binds the ID token to this attempt."""
    client = AsyncOAuth2Client(
        connection.client_id,
        connection.client_secret,
        scope="openid email profile",
        redirect_uri=redirect_uri,
    )
    url, _ = client.create_authorization_url(
        str(discovery["authorization_endpoint"]), state=state, nonce=nonce
    )
    return str(url)


async def exchange_code(
    discovery: dict[str, Any],
    connection: SsoConnection,
    *,
    code: str,
    redirect_uri: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """The raw ID token for an authorization code.

    `transport` is the test seam the OAuth module already uses (see
    `_AuthlibOAuthClient`): a real caller never passes it, so production
    always talks to the real IdP over the network.
    """
    kwargs: dict[str, Any] = {
        "scope": "openid email profile",
        "redirect_uri": redirect_uri,
        "timeout": _TOKEN_TIMEOUT_SECONDS,
    }
    if transport is not None:
        kwargs["transport"] = transport
    async with AsyncOAuth2Client(
        connection.client_id, connection.client_secret, **kwargs
    ) as client:
        try:
            token = await client.fetch_token(
                str(discovery["token_endpoint"]), code=code, grant_type="authorization_code"
            )
        # Three trees, not one. `OAuth2Error` is authlib's *protocol* error and
        # `OAuthError` is its *client-integration* error -- they share no base
        # beyond Exception, and it is the second one an IdP refusing the code
        # actually produces. Catching only the first let an `invalid_grant`
        # escape this module as a 500, throwing away both the 401 and the
        # message that tells an operator the code was rejected (T-350).
        except (httpx.HTTPError, OAuth2Error, OAuthError) as exc:
            raise AuthenticationError(
                "errors.sso_exchange_failed",
                fallback="The identity provider rejected the sign-in attempt",
            ) from exc
    id_token = token.get("id_token")
    if not id_token:
        # A token response with no ID token is an OAuth2 response, not an
        # OIDC one -- usually a connection configured without the `openid`
        # scope, which is worth saying rather than failing on a KeyError.
        raise AuthenticationError(
            "errors.sso_no_id_token",
            fallback="The identity provider returned no ID token",
        )
    return str(id_token)


async def verify_id_token(
    redis: Redis,
    discovery: dict[str, Any],
    connection: SsoConnection,
    *,
    id_token: str,
    nonce: str,
) -> SsoIdentity:
    """The identity the ID token asserts, or `AuthenticationError`.

    See the module docstring for the five checks and why each is separate.
    """
    claims = await _verified_claims(redis, discovery, connection, id_token=id_token)

    if claims.get("iss") != connection.issuer:
        raise _rejected("errors.sso_bad_issuer", "The ID token came from a different issuer")
    if not _audience_matches(claims.get("aud"), connection.client_id):
        raise _rejected("errors.sso_bad_audience", "The ID token was issued for another client")
    if claims.get("nonce") != nonce:
        raise _rejected("errors.sso_bad_nonce", "The ID token does not match this sign-in attempt")

    _validate_time_claims(claims)

    email = claims.get("email")
    if not isinstance(email, str) or not email:
        raise _rejected("errors.sso_no_email", "The identity provider returned no email address")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise _rejected("errors.sso_no_subject", "The ID token carried no subject")

    return SsoIdentity(
        subject=subject,
        email=email.strip().lower(),
        full_name=_optional_str(claims.get("name")),
        groups=_groups(claims, connection.group_claim),
        asserted_mfa=_asserted_mfa(claims),
    )


async def _verified_claims(
    redis: Redis,
    discovery: dict[str, Any],
    connection: SsoConnection,  # noqa: ARG001 -- kept for symmetry with the callers below
    *,
    id_token: str,
) -> dict[str, Any]:
    """Signature-checked claims, re-fetching the JWKS once on failure.

    The retry is what stops an IdP key rotation from locking an organization
    out for the rest of the cache TTL, and it is exactly one: an attacker who
    can make verification fail can make this product fetch, so it must not be
    a fetch per attempt. See discovery.py's docstring.
    """
    for force in (False, True):
        jwks = await discovery_module.fetch_jwks(redis, discovery, force=force)
        try:
            # `jwks` is the parsed JWKS document; the stub types
            # import_key_set's argument as a TypedDict this dict does not
            # statically satisfy, and a cast is more honest than reshaping a
            # document we do not own.
            key_set = KeySet.import_key_set(cast("Any", jwks))
            token = jwt.decode(id_token, key_set, algorithms=list(ALLOWED_ALGORITHMS))
        except (JoseError, ValueError):
            continue
        return dict(token.claims)
    raise _rejected("errors.sso_bad_signature", "The ID token's signature did not verify")


def _validate_time_claims(claims: dict[str, Any]) -> None:
    import time  # noqa: PLC0415 -- only this function needs a clock

    now = int(time.time())
    exp = claims.get("exp")
    if not isinstance(exp, int) or exp + CLOCK_SKEW_SECONDS < now:
        raise _rejected("errors.sso_expired", "The ID token has expired")
    iat = claims.get("iat")
    # An `iat` in the future beyond the skew means the two clocks disagree by
    # more than this product is willing to tolerate -- accepting it would
    # widen the window in which a captured token is still fresh.
    if isinstance(iat, int) and iat - CLOCK_SKEW_SECONDS > now:
        raise _rejected("errors.sso_expired", "The ID token is not valid yet")


def _audience_matches(aud: object, client_id: str) -> bool:
    # `aud` is a string or an array of strings; both are legal OIDC.
    if isinstance(aud, str):
        return aud == client_id
    if isinstance(aud, list):
        return client_id in aud
    return False


def _groups(claims: dict[str, Any], group_claim: str | None) -> tuple[str, ...]:
    if not group_claim:
        return ()
    value = claims.get(group_claim)
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return ()


def _asserted_mfa(claims: dict[str, Any]) -> bool:
    """Whether the IdP says it already checked a second factor.

    Read from `amr` (the values RFC 8176 registers for a second factor) or
    from an `acr` naming one of the two levels people actually configure.
    Anything unrecognised is `False`, which means this product asks for TOTP
    itself -- an unfamiliar claim must never *lower* a bar, only fail to
    raise one.
    """
    amr = claims.get("amr")
    if isinstance(amr, list):
        methods = {str(item).lower() for item in amr}
        if methods & {"mfa", "otp", "hwk", "sms", "swk", "pop", "face", "fpt", "iris"}:
            return True
    acr = claims.get("acr")
    return isinstance(acr, str) and acr.lower() in _MFA_ACR_VALUES


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _rejected(message_key: str, fallback: str) -> AuthenticationError:
    return AuthenticationError(message_key, fallback=fallback)
