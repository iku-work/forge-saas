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


def hash_password(plain: str) -> str:
    return _password_hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _password_hasher.verify(hashed, plain)
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


def create_access_token(user_id: UUID, org_id: UUID | None, permissions: Sequence[str]) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "org": str(org_id) if org_id is not None else None,
        "perms": list(permissions),
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        "jti": str(uuid4()),
    }
    return jwt.encode(claims, settings.JWT_PRIVATE_KEY, algorithm=_JWT_ALGORITHM)


def verify_access_token(token: str) -> AccessTokenPayload:
    settings = get_settings()
    try:
        # `algorithms` is restricted to RS256 explicitly — without this,
        # a token forged with `alg: none` or `alg: HS256` (using the public
        # key as an HMAC secret) would otherwise be accepted.
        claims = jwt.decode(token, settings.JWT_PUBLIC_KEY, algorithms=[_JWT_ALGORITHM])
        org = claims.get("org")
        return AccessTokenPayload(
            user_id=UUID(claims["sub"]),
            org_id=UUID(org) if org is not None else None,
            permissions=tuple(claims.get("perms", [])),
            jti=UUID(claims["jti"]),
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
