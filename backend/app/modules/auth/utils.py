"""
Every cryptographic primitive used by the auth module lives here — password
hashing, RS256 JWT access tokens, refresh-token opaque/hash pairs, single-use
token hashing, and signed URL tokens. No other module should import argon2,
jose, or itsdangerous directly.
"""

import hashlib
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from itsdangerous import BadSignature, URLSafeTimedSerializer
from jose import JWTError, jwt

from app.core.config import get_settings
from app.core.exceptions import AuthenticationError

_JWT_ALGORITHM = "RS256"

_password_hasher = PasswordHasher()


def _peppered(plain: str) -> str:
    # T-225/ASVS V2.4.5: an HMAC-SHA256 pre-hash keyed by settings.PASSWORD_PEPPER,
    # applied identically before both hash() and verify() so the pepper is
    # transparent to argon2-cffi itself. Blank pepper (dev only — enforced
    # non-blank outside dev by Settings._enforce_startup_invariants) reduces
    # to hashing the plaintext directly, i.e. today's behavior.
    pepper = get_settings().PASSWORD_PEPPER
    return hashlib.sha256(f"{pepper}:{plain}".encode()).hexdigest()


def hash_password(plain: str) -> str:
    return _password_hasher.hash(_peppered(plain))


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _password_hasher.verify(hashed, _peppered(plain))
    except VerifyMismatchError:
        return False
    # Any other exception (e.g. InvalidHashError for a corrupt/foreign hash
    # format) propagates — that is a data-integrity bug, not a wrong password.


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


def create_access_token(  # noqa: PLR0913 -- T-218 adds auth_time on top of T-142's existing impersonator_id/expire_minutes
    user_id: UUID,
    org_id: UUID | None,
    permissions: Sequence[str],
    *,
    impersonator_id: UUID | None = None,
    expire_minutes: int | None = None,
    auth_time: datetime | None = None,
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
    if impersonator_id is not None:
        claims["impersonator_id"] = str(impersonator_id)
    return jwt.encode(claims, settings.JWT_PRIVATE_KEY, algorithm=_JWT_ALGORITHM)


def verify_access_token(token: str) -> AccessTokenPayload:
    settings = get_settings()
    try:
        # `algorithms` is restricted to RS256 explicitly — without this,
        # a token forged with `alg: none` or `alg: HS256` (using the public
        # key as an HMAC secret) would otherwise be accepted.
        claims = jwt.decode(token, settings.JWT_PUBLIC_KEY, algorithms=[_JWT_ALGORITHM])
        org = claims.get("org")
        impersonator_id = claims.get("impersonator_id")
        auth_time = claims.get("auth_time")
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


def _url_token_serializer() -> URLSafeTimedSerializer:
    # itsdangerous needs an opaque signing secret; Settings has no dedicated
    # field for one (§"Configuration module"), so JWT_PRIVATE_KEY is reused.
    # The "auth.url-token" salt namespaces the derived key so a leaked
    # signature here can't be replayed as a JWT signature, or vice versa.
    settings = get_settings()
    return URLSafeTimedSerializer(settings.JWT_PRIVATE_KEY, salt="auth.url-token")


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
