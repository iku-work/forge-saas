from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from argon2.exceptions import InvalidHashError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt

from app.core.config import get_settings
from app.core.exceptions import AuthenticationError
from app.modules.auth.utils import (
    AccessTokenPayload,
    check_password,
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


def _generate_rsa_keypair() -> tuple[str, str]:
    """A throwaway keypair for rotation tests. 2048-bit: these are generated
    per test and the suite runs on every push, so key size is wall time."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private, public


@pytest.mark.owasp("A02")
def test_verify_password_fails_when_pepper_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    # T-225/ASVS V2.4.5: proves PASSWORD_PEPPER is actually mixed into the
    # hash, not just present as an unused setting -- a hash produced under
    # one pepper must not verify under a different one.
    settings = get_settings()
    monkeypatch.setattr(settings, "PASSWORD_PEPPER", "pepper-a")
    # Explicit: with PASSWORD_PEPPER_OLD set this would (correctly) verify --
    # that is the rotation path, covered below. This asserts the pepper is
    # load-bearing, so the fallback has to be off for it to mean anything.
    monkeypatch.setattr(settings, "PASSWORD_PEPPER_OLD", "")
    hashed = hash_password("correct horse battery staple")

    monkeypatch.setattr(settings, "PASSWORD_PEPPER", "pepper-b")

    assert verify_password("correct horse battery staple", hashed) is False


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
        auth_time=payload.auth_time,
        expires_at=payload.expires_at,
    )
    assert isinstance(payload.jti, UUID)
    # T-218: defaults to this token's own mint time when the caller doesn't
    # pass one explicitly -- see create_access_token's auth_time param.
    assert payload.auth_time is not None


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
# Impersonation claims (T-142)
# ---------------------------------------------------------------------------


def test_access_token_round_trips_impersonator_id() -> None:
    user_id = uuid4()
    impersonator_id = uuid4()

    token = create_access_token(
        user_id, None, [], impersonator_id=impersonator_id, expire_minutes=15
    )
    payload = verify_access_token(token)

    assert payload.user_id == user_id
    assert payload.impersonator_id == impersonator_id


def test_access_token_without_impersonator_id_defaults_to_none() -> None:
    token = create_access_token(uuid4(), None, [])

    payload = verify_access_token(token)

    assert payload.impersonator_id is None


def test_create_access_token_expire_minutes_overrides_default() -> None:
    # A negative override mints an already-expired token -- proves
    # expire_minutes actually controls `exp` rather than being ignored, which
    # is what T-142's impersonation hard cap relies on.
    token = create_access_token(uuid4(), None, [], expire_minutes=-1)

    with pytest.raises(AuthenticationError):
        verify_access_token(token)


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


# ---------------------------------------------------------------------------
# Pepper rotation (PASSWORD_PEPPER_OLD).
# ---------------------------------------------------------------------------


@pytest.mark.owasp("A02")
def test_a_password_hashed_under_the_previous_pepper_still_verifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of the fallback.

    Without it PASSWORD_PEPPER could not be rotated at all: changing it made
    every stored password fail to verify, indistinguishably from a wrong
    password, and the only remedy was forcing a reset on every user.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "PASSWORD_PEPPER", "pepper-a")
    monkeypatch.setattr(settings, "PASSWORD_PEPPER_OLD", "")
    hashed = hash_password("correct horse battery staple")

    monkeypatch.setattr(settings, "PASSWORD_PEPPER", "pepper-b")
    monkeypatch.setattr(settings, "PASSWORD_PEPPER_OLD", "pepper-a")

    result = check_password("correct horse battery staple", hashed)
    assert result.ok is True
    # ...and the caller is told to rewrite it, which is what ends the
    # rotation rather than leaving the fallback load-bearing forever.
    assert result.needs_rehash is True


@pytest.mark.owasp("A02")
def test_the_wrong_password_is_still_wrong_under_both_peppers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The obvious way to break this: a fallback that turns into "accept
    # anything" because the second attempt's failure is swallowed.
    settings = get_settings()
    monkeypatch.setattr(settings, "PASSWORD_PEPPER", "pepper-a")
    monkeypatch.setattr(settings, "PASSWORD_PEPPER_OLD", "")
    hashed = hash_password("correct horse battery staple")

    monkeypatch.setattr(settings, "PASSWORD_PEPPER", "pepper-b")
    monkeypatch.setattr(settings, "PASSWORD_PEPPER_OLD", "pepper-a")

    assert check_password("not the password", hashed).ok is False


def test_a_current_pepper_hash_needs_no_rehash(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "PASSWORD_PEPPER", "pepper-a")
    monkeypatch.setattr(settings, "PASSWORD_PEPPER_OLD", "pepper-z")
    hashed = hash_password("correct horse battery staple")

    result = check_password("correct horse battery staple", hashed)
    assert result.ok is True
    assert result.needs_rehash is False


# ---------------------------------------------------------------------------
# Token-signing rotation: JWT_PUBLIC_KEY_OLD and URL_TOKEN_SECRET(_OLD).
# ---------------------------------------------------------------------------


def test_url_tokens_still_use_the_jwt_key_when_no_dedicated_secret_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default has to stay exactly what it was.

    Every deployment predating URL_TOKEN_SECRET has links in flight signed
    with JWT_PRIVATE_KEY; adopting a dedicated secret silently would
    invalidate all of them at the moment of deploy.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "URL_TOKEN_SECRET", "")
    monkeypatch.setattr(settings, "URL_TOKEN_SECRET_OLD", "")

    token = sign_url_token({"purpose": "test"}, 60)
    assert verify_url_token(token, 60) == {"purpose": "test"}


def test_a_url_token_survives_rotating_its_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """The point of the change: an emailed link outlives the rotation.

    Email verification links last 24 hours, so a rotation without overlap
    breaks every one issued in the previous day -- password resets included.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "URL_TOKEN_SECRET", "secret-one")
    monkeypatch.setattr(settings, "URL_TOKEN_SECRET_OLD", "")
    issued_before = sign_url_token({"purpose": "reset"}, 3600)

    monkeypatch.setattr(settings, "URL_TOKEN_SECRET", "secret-two")
    monkeypatch.setattr(settings, "URL_TOKEN_SECRET_OLD", "secret-one")

    assert verify_url_token(issued_before, 3600) == {"purpose": "reset"}
    # And new links are signed with the new secret, so clearing the old one
    # later does not strand them.
    issued_after = sign_url_token({"purpose": "reset"}, 3600)
    monkeypatch.setattr(settings, "URL_TOKEN_SECRET_OLD", "")
    assert verify_url_token(issued_after, 3600) == {"purpose": "reset"}


@pytest.mark.owasp("A02")
def test_a_url_token_signed_with_an_unrelated_secret_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The over-correction guard: accepting a list of keys must not become
    # accepting anything.
    settings = get_settings()
    monkeypatch.setattr(settings, "URL_TOKEN_SECRET", "attacker-secret")
    monkeypatch.setattr(settings, "URL_TOKEN_SECRET_OLD", "")
    forged = sign_url_token({"purpose": "reset"}, 3600)

    monkeypatch.setattr(settings, "URL_TOKEN_SECRET", "real-secret")
    monkeypatch.setattr(settings, "URL_TOKEN_SECRET_OLD", "previous-real-secret")

    with pytest.raises(AuthenticationError):
        verify_url_token(forged, 3600)


def test_an_access_token_survives_rotating_the_keypair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify-side overlap, so a keypair rotation does not reject every
    token minted in the preceding 15 minutes at once."""
    settings = get_settings()
    old_public = settings.JWT_PUBLIC_KEY
    token = create_access_token(uuid4(), None, [])

    # Rotate to a different keypair, keeping the old public half for
    # verification only.
    new_private, new_public = _generate_rsa_keypair()
    monkeypatch.setattr(settings, "JWT_PRIVATE_KEY", new_private)
    monkeypatch.setattr(settings, "JWT_PUBLIC_KEY", new_public)
    monkeypatch.setattr(settings, "JWT_PUBLIC_KEY_OLD", old_public)

    assert verify_access_token(token).user_id is not None

    # ...and once the old public key is dropped, it stops being accepted.
    monkeypatch.setattr(settings, "JWT_PUBLIC_KEY_OLD", "")
    with pytest.raises(AuthenticationError):
        verify_access_token(token)
