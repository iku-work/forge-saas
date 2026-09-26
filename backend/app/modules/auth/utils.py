"""
Every cryptographic primitive used by the auth module lives here — password
hashing, RS256 JWT access tokens, refresh-token opaque/hash pairs, single-use
token hashing, and signed URL tokens. No other module should import argon2,
jose, or itsdangerous directly.
"""

import hashlib
import secrets
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from itsdangerous import BadSignature, URLSafeTimedSerializer
from jose import JWTError, jwt

from app.core.config import get_settings
from app.core.exceptions import AuthenticationError

if TYPE_CHECKING:
    from app.core.config import Settings

_JWT_ALGORITHM = "RS256"

_password_hasher = PasswordHasher()


def _peppered(plain: str, pepper: str) -> str:
    # T-225/ASVS V2.4.5: an HMAC-SHA256 pre-hash keyed by the pepper, applied
    # identically before both hash() and verify() so it is transparent to
    # argon2-cffi itself. Blank pepper (dev only — enforced non-blank outside
    # dev by Settings._enforce_startup_invariants) reduces to hashing the
    # plaintext directly.
    return hashlib.sha256(f"{pepper}:{plain}".encode()).hexdigest()


def hash_password(plain: str) -> str:
    """Always hashes with the *current* pepper. Rotation moves rows forward
    one successful login at a time; see check_password."""
    return _password_hasher.hash(_peppered(plain, get_settings().PASSWORD_PEPPER))


@dataclass(frozen=True)
class PasswordCheck:
    """Whether the password matched, and whether its stored hash is stale."""

    ok: bool
    needs_rehash: bool


def check_password(plain: str, hashed: str) -> PasswordCheck:
    """Verify against the current pepper, then the previous one.

    Before this, PASSWORD_PEPPER could not be rotated at all: it is HMAC'd
    into every password before Argon2, with no second pepper and no version
    marker on the stored hash, so changing it made every password fail to
    verify -- indistinguishably from a wrong password. The only remedy was a
    forced reset for every user. `docs/runbooks/credential-rotation.md` said
    so in as many words.

    The fallback is what makes rotation possible, and `needs_rehash` is what
    makes it *finish*: the caller re-hashes with the current pepper on the
    next successful login, so the population migrates itself and
    PASSWORD_PEPPER_OLD can be dropped once logins have drained.

    Two costs, both deliberate:

    - While PASSWORD_PEPPER_OLD is set, a *wrong* password costs two Argon2
      verifications rather than one, and Argon2 is memory-hard on purpose.
      That is why the fallback is skipped entirely when the setting is blank
      -- which is its normal state -- rather than being always-on.
    - A password verified under the old pepper is still correct, so this
      returns ok=True. Refusing it would be the "rotation breaks every
      login" behaviour this exists to remove.

    `needs_rehash` also covers argon2's own parameter changes, so raising the
    cost parameters migrates hashes by the same mechanism.
    """
    settings = get_settings()
    try:
        _password_hasher.verify(hashed, _peppered(plain, settings.PASSWORD_PEPPER))
    except VerifyMismatchError:
        previous = settings.PASSWORD_PEPPER_OLD
        if not previous:
            return PasswordCheck(ok=False, needs_rehash=False)
        try:
            _password_hasher.verify(hashed, _peppered(plain, previous))
        except VerifyMismatchError:
            return PasswordCheck(ok=False, needs_rehash=False)
        return PasswordCheck(ok=True, needs_rehash=True)
    # Any other exception (e.g. InvalidHashError for a corrupt/foreign hash
    # format) propagates — that is a data-integrity bug, not a wrong password.
    return PasswordCheck(ok=True, needs_rehash=_password_hasher.check_needs_rehash(hashed))


def verify_password(plain: str, hashed: str) -> bool:
    """The boolean-only view, for callers with nothing to re-hash.

    2FA backup codes go through here: they are single-use and deleted on
    redemption, so there is no row worth rewriting.
    """
    return check_password(plain, hashed).ok


# T-223: argon2's memory-hard params (T-225) make a single hash/verify call
# take tens of milliseconds of pure CPU time. auth/service.py's request
# handlers run these off the event loop via asyncio.to_thread (see that
# module's _hash_password_off_loop/_verify_password_off_loop) rather than
# calling hash_password/verify_password directly -- k6's login-burst.js is
# what actually surfaced why: called synchronously inside an async def, a
# blocking call like this stalls the whole single-threaded event loop for
# its duration, serializing every other in-flight request on this worker
# behind it. 30 concurrent logins pushed p(95) past 1.8s even though one
# hash in isolation only takes ~45ms. Kept synchronous here (not async) so
# every other caller -- seed scripts, the ~60 test files across
# backend/tests/ that construct a User directly -- doesn't have to change.


@dataclass(frozen=True, slots=True)
class AccessTokenPayload:
    user_id: UUID
    org_id: UUID | None
    permissions: tuple[str, ...]
    jti: UUID
    # T-142: set only on a superadmin-minted impersonation token, naming the
    # real superadmin acting behind `user_id` (the impersonated target).
    # `None` on every ordinary login/refresh-issued token.
    impersonator_id: UUID | None = None
    # T-218: when the underlying session's credentials were last actually
    # verified (login/2FA/WebAuthn/magic-link) -- carried through unchanged
    # across refresh()-driven rotations, unlike `iat` (which is this
    # specific access token's own mint time, refreshed every 15 minutes).
    # `require_recent_auth` (app.core.dependencies) reads this to gate
    # step-up-protected actions. Defaults to `iat` for any token that
    # predates this field (e.g. one already in flight during a deploy).
    auth_time: datetime | None = None
    # T-261: the `exp` claim, surfaced because "how long is left on this
    # session" is something some callers have to *show* rather than merely
    # enforce -- the mobile impersonation banner counts down against it.
    # kw_only so it can be required (unlike the two optional fields above it)
    # without having to sit ahead of them in the field order: create_access_token
    # always sets `exp`, so there is no token this can legitimately be missing
    # from, and a default would only ever paper over a decode bug.
    expires_at: datetime = field(kw_only=True)
    # The Session row this token was minted from, so a revoked session can
    # take its already-issued access tokens down with it rather than leaving
    # them usable until they expire (repository audit finding 6: "sign out
    # all devices" was not containment, it was a 15-minute promise).
    #
    # `None` has two legitimate meanings and both must keep working:
    #   - an impersonation token (T-142), which is deliberately issued with
    #     no refresh token and therefore has no session at all. It is revoked
    #     through the jti blocklist instead.
    #   - a token minted before this claim existed, still in flight during a
    #     deploy. Those drain within one access-token lifetime.
    sid: UUID | None = field(default=None, kw_only=True)


def create_access_token(  # noqa: PLR0913 -- T-218 adds auth_time on top of T-142's existing impersonator_id/expire_minutes
    user_id: UUID,
    org_id: UUID | None,
    permissions: Sequence[str],
    *,
    impersonator_id: UUID | None = None,
    expire_minutes: int | None = None,
    auth_time: datetime | None = None,
    sid: UUID | None = None,
) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    ttl_minutes = (
        expire_minutes if expire_minutes is not None else settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "org": str(org_id) if org_id is not None else None,
        "perms": list(permissions),
        "iat": now,
        "exp": now + timedelta(minutes=ttl_minutes),
        "jti": str(uuid4()),
        # jose's datetime->epoch auto-conversion (in jwt.encode) only covers
        # "exp"/"iat"/"nbf" by name, so this claim needs converting by hand
        # or json.dumps chokes on a raw datetime.
        "auth_time": (auth_time or now).timestamp(),
    }
    if sid is not None:
        # Omitted rather than set to null for an impersonation token: absence
        # is what get_current_user reads as "this token has no session to
        # check", and a null would have to mean the same thing twice.
        claims["sid"] = str(sid)
    if impersonator_id is not None:
        claims["impersonator_id"] = str(impersonator_id)
    return jwt.encode(claims, settings.JWT_PRIVATE_KEY, algorithm=_JWT_ALGORITHM)


_BEARER_PREFIX = "Bearer "


def bearer_token(authorization: str | None) -> str | None:
    """The token out of an `Authorization: Bearer <token>` header value.

    Exists so the two places that need the raw token *outside* FastAPI's
    dependency system -- app.middleware.impersonation (which runs before
    routing) and app.core.dependencies.reject_during_impersonation (which
    must not advertise a security scheme on the public endpoints it guards,
    or every generated client would think /auth/forgot-password needs a
    token) -- don't each reimplement the prefix check.
    """
    if authorization is None or not authorization.startswith(_BEARER_PREFIX):
        return None
    return authorization[len(_BEARER_PREFIX) :]


def _decode_with_rotation(token: str, settings: "Settings") -> dict[str, Any]:
    """Decode against the current public key, then the one being retired.

    Signing only ever uses the current private key: this is verify-side
    overlap, so a keypair rotation does not reject every access token minted
    in the preceding 15 minutes all at once. Without it, rotation produced a
    burst in which every client discovered its token was invalid
    simultaneously and refreshed together.

    `algorithms` stays pinned on both attempts -- without it a token forged
    with `alg: none`, or `alg: HS256` using the public key as an HMAC
    secret, would be accepted.
    """
    try:
        return jwt.decode(token, settings.JWT_PUBLIC_KEY, algorithms=[_JWT_ALGORITHM])
    except JWTError:
        if not settings.JWT_PUBLIC_KEY_OLD:
            raise
        return jwt.decode(token, settings.JWT_PUBLIC_KEY_OLD, algorithms=[_JWT_ALGORITHM])


def verify_access_token(token: str) -> AccessTokenPayload:
    settings = get_settings()
    try:
        # `algorithms` is restricted to RS256 explicitly — without this,
        # a token forged with `alg: none` or `alg: HS256` (using the public
        # key as an HMAC secret) would otherwise be accepted.
        claims = _decode_with_rotation(token, settings)
        org = claims.get("org")
        impersonator_id = claims.get("impersonator_id")
        auth_time = claims.get("auth_time")
        sid = claims.get("sid")
        return AccessTokenPayload(
            user_id=UUID(claims["sub"]),
            org_id=UUID(org) if org is not None else None,
            permissions=tuple(claims.get("perms", [])),
            jti=UUID(claims["jti"]),
            impersonator_id=UUID(impersonator_id) if impersonator_id is not None else None,
            auth_time=(
                datetime.fromtimestamp(auth_time, tz=UTC)
                if auth_time is not None
                else datetime.fromtimestamp(claims["iat"], tz=UTC)
            ),
            expires_at=datetime.fromtimestamp(claims["exp"], tz=UTC),
            sid=UUID(sid) if sid is not None else None,
        )
    except (JWTError, KeyError, ValueError, TypeError) as exc:
        raise AuthenticationError(
            "errors.invalid_access_token", fallback="Invalid or expired access token"
        ) from exc


def generate_refresh_token() -> tuple[str, str]:
    plain = secrets.token_urlsafe(32)
    return plain, hash_token(plain)


def hash_token(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def _url_token_keys() -> list[str]:
    """Every secret a URL token may be signed with, oldest first.

    itsdangerous signs with the **last** key in the list and verifies against
    all of them, which is the rotation primitive this needs. Measured against
    itsdangerous 2.2 rather than assumed.

    Blank URL_TOKEN_SECRET means the historical behaviour: these tokens are
    signed with JWT_PRIVATE_KEY, which `_url_token_serializer` borrowed for
    want of a dedicated field. That coupling is the reason rotating the JWT
    keypair invalidated every emailed link -- password resets, email
    verification, magic links, the OAuth `state` -- rather than just the
    15-minute access tokens.

    The migration is not automatic on purpose. Adopting a dedicated secret
    silently would invalidate every link in flight at the moment of deploy;
    instead the operator sets URL_TOKEN_SECRET to the new value and
    URL_TOKEN_SECRET_OLD to the JWT private key for one token lifetime. See
    docs/runbooks/credential-rotation.md.
    """
    settings = get_settings()
    if not settings.URL_TOKEN_SECRET:
        return [settings.JWT_PRIVATE_KEY]
    previous = settings.URL_TOKEN_SECRET_OLD
    return ([previous] if previous else []) + [settings.URL_TOKEN_SECRET]


def _url_token_serializer() -> URLSafeTimedSerializer:
    # The "auth.url-token" salt namespaces the derived key so a leaked
    # signature here can't be replayed as a JWT signature, or vice versa --
    # which matters most while the two still share a secret.
    return URLSafeTimedSerializer(_url_token_keys(), salt="auth.url-token")


def sign_url_token(payload: dict[str, Any], ttl: int) -> str:  # noqa: ARG001 -- ttl is enforced by verify_url_token's max_age (itsdangerous checks age at load time, not sign time); kept on this signature so call sites pair the two values explicitly
    return _url_token_serializer().dumps(payload)


def verify_url_token(token: str, max_age: int) -> dict[str, Any]:
    try:
        result: dict[str, Any] = _url_token_serializer().loads(token, max_age=max_age)
    except BadSignature as exc:
        raise AuthenticationError(
            "errors.invalid_token", fallback="Invalid or expired token"
        ) from exc
    return result
