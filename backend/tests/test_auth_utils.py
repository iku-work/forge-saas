from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from argon2.exceptions import InvalidHashError
from jose import jwt

from app.core.config import get_settings
from app.core.exceptions import AuthenticationError
from app.modules.auth.utils import (
    AccessTokenPayload,
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_token,
    sign_url_token,
    verify_access_token,
    verify_password,
    verify_url_token,
)

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def test_hash_password_round_trips_with_verify_password() -> None:
    hashed = hash_password("correct horse battery staple")

    assert verify_password("correct horse battery staple", hashed) is True


def test_verify_password_returns_false_on_mismatch() -> None:
    hashed = hash_password("correct horse battery staple")

    assert verify_password("wrong password", hashed) is False


def test_verify_password_raises_on_corrupt_hash_format() -> None:
    with pytest.raises(InvalidHashError):
        verify_password("anything", "not-an-argon2-hash")


# ---------------------------------------------------------------------------
# Access tokens (RS256 JWT)
# ---------------------------------------------------------------------------


def test_access_token_round_trips_claims() -> None:
    user_id = uuid4()
    org_id = uuid4()

    token = create_access_token(user_id, org_id, ["members:read", "billing:read"])
    payload = verify_access_token(token)

    assert payload == AccessTokenPayload(
        user_id=user_id,
        org_id=org_id,
        permissions=("members:read", "billing:read"),
        jti=payload.jti,
    )
    assert isinstance(payload.jti, UUID)


def test_access_token_round_trips_with_no_org() -> None:
    token = create_access_token(uuid4(), None, [])

    payload = verify_access_token(token)

    assert payload.org_id is None


def test_access_token_jti_is_unique_per_token() -> None:
    user_id = uuid4()

    first = verify_access_token(create_access_token(user_id, None, []))
    second = verify_access_token(create_access_token(user_id, None, []))

    assert first.jti != second.jti


def test_expired_access_token_raises_authentication_error() -> None:
    settings = get_settings()
    now = datetime.now(UTC)
    claims = {
        "sub": str(uuid4()),
        "org": None,
        "perms": [],
        "iat": now - timedelta(minutes=20),
        "exp": now - timedelta(minutes=5),
        "jti": str(uuid4()),
    }
    expired_token = jwt.encode(claims, settings.JWT_PRIVATE_KEY, algorithm="RS256")

    with pytest.raises(AuthenticationError):
        verify_access_token(expired_token)


def test_access_token_with_wrong_algorithm_raises_authentication_error() -> None:
    now = datetime.now(UTC)
    claims = {
        "sub": str(uuid4()),
        "org": None,
        "perms": [],
        "iat": now,
        "exp": now + timedelta(minutes=15),
        "jti": str(uuid4()),
    }
    # A decoder that doesn't pin `algorithms=["RS256"]` would happily verify
    # a token signed with a different algorithm (the classic alg-confusion
    # attack vector) — python-jose itself refuses to sign HS256 with a PEM
    # key as the secret, so this uses an arbitrary HMAC secret instead.
    forged_token = jwt.encode(claims, "attacker-controlled-secret", algorithm="HS256")

    with pytest.raises(AuthenticationError):
        verify_access_token(forged_token)


def test_garbage_access_token_raises_authentication_error() -> None:
    with pytest.raises(AuthenticationError):
        verify_access_token("not.a.jwt")


# ---------------------------------------------------------------------------
# Refresh tokens / single-use token hashing
# ---------------------------------------------------------------------------


def test_generate_refresh_token_returns_distinct_plain_and_hash() -> None:
    plain, stored_hash = generate_refresh_token()

    assert plain != stored_hash
    assert hash_token(plain) == stored_hash


def test_generate_refresh_token_is_random_per_call() -> None:
    first, _ = generate_refresh_token()
    second, _ = generate_refresh_token()

    assert first != second


def test_hash_token_is_deterministic() -> None:
    assert hash_token("same-input") == hash_token("same-input")


# ---------------------------------------------------------------------------
# Signed URL tokens (itsdangerous)
# ---------------------------------------------------------------------------


def test_url_token_round_trips_payload() -> None:
    token = sign_url_token({"email": "alice@example.com"}, ttl=3600)

    assert verify_url_token(token, max_age=3600) == {"email": "alice@example.com"}


def test_expired_url_token_raises_authentication_error() -> None:
    token = sign_url_token({"x": 1}, ttl=1)

    with pytest.raises(AuthenticationError):
        verify_url_token(token, max_age=-1)


def test_tampered_url_token_raises_authentication_error() -> None:
    token = sign_url_token({"x": 1}, ttl=3600)
    # Flips the second-to-last character rather than the last: base64's final
    # character can carry unused padding bits, so occasionally two different
    # last characters decode to the same bytes and the "tampered" signature
    # is bitwise identical to the original -- flaky on roughly 1 run in 4.
    tampered = token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1]

    with pytest.raises(AuthenticationError):
        verify_url_token(tampered, max_age=3600)
