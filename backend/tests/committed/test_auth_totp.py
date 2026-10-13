"""
Verifies T-024 (TOTP 2FA): the enable -> verify -> disable round trip,
single-use backup codes, and the modified /login flow where a totp_enabled
user gets a `{pending_2fa, temp_token}` challenge instead of real tokens and
must trade it for one at POST /auth/2fa/login.

Uses the same real-Postgres (+ real-Redis for the router-level tests)
testcontainers pattern as test_auth_oauth.py: AuthService methods are
exercised directly against a plain session for the service-level tests below,
and the full HTTP surface is exercised via httpx.AsyncClient(transport=
ASGITransport(...)) for the router-level ones, with the slowapi limiter's
storage swapped to a disposable Redis the same way test_auth_router.py does.
"""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
import pyotp
import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from app.core.exceptions import AuthenticationError, ConflictError, NotFoundError
from app.modules.auth import amr
from app.modules.auth import service as auth_service_module
from app.modules.auth.models import Session as SessionModel
from app.modules.auth.models import User
from app.modules.auth.repository import TOTPSecretRepository, UserRepository
from app.modules.auth.schemas import LoginRequest
from app.modules.auth.service import AuthService, TwoFactorChallenge
from app.modules.auth.utils import (
    create_access_token,
    hash_backup_code,
    hash_password,
    is_legacy_backup_code,
    sign_url_token,
    verify_access_token,
)
from tests._auth import session_token

_VALID_PASSWORD = "Correct-Horse9"
_IP = "127.0.0.1"
_USER_AGENT = "pytest"


def _current_code(secret: str) -> str:
    return pyotp.TOTP(secret).now()


def _next_code(secret: str) -> str:
    """A code for the *next* 30s step -- used when a test needs two
    legitimate (non-replay) verifications of the same secret in quick
    succession, since T-218's same-step replay tracking (AuthService.
    _verify_totp_and_track_replay) now rejects submitting _current_code's
    exact value twice."""
    return pyotp.TOTP(secret).at(datetime.now(UTC), 1)


# --- service-level fixtures (Postgres only) -------------------------------------


@pytest.fixture
def service(session: AsyncSession) -> AuthService:
    return AuthService(session)


@pytest.fixture
def user_repo(session: AsyncSession) -> UserRepository:
    return UserRepository(session)


@pytest.fixture
def totp_repo(session: AsyncSession) -> TOTPSecretRepository:
    return TOTPSecretRepository(session)


@pytest.fixture
async def verified_user(user_repo: UserRepository, session: AsyncSession) -> User:
    # Committed (not just flushed): the router-level tests below hit this
    # user through brand-new sessions opened per HTTP request, which can't
    # see a write still sitting in this fixture's own uncommitted transaction.
    user = await user_repo.create(
        email="alice@example.com",
        hashed_password=hash_password(_VALID_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    await session.commit()
    return user


async def _enable_and_verify(service: AuthService, user: User) -> tuple[str, list[str]]:
    """Drives enable_2fa -> verify_2fa to a fully-activated state and hands
    back (secret, backup_codes) for the caller to exercise further."""
    _, secret = await service.enable_2fa(user)
    backup_codes = await service.verify_2fa(user, _current_code(secret))
    return secret, backup_codes


# --- enable_2fa ------------------------------------------------------------------


async def test_enable_2fa_returns_provisioning_uri_and_secret(
    service: AuthService, verified_user: User
) -> None:
    provisioning_uri, secret = await service.enable_2fa(verified_user)

    assert secret
    assert provisioning_uri.startswith("otpauth://totp/")
    assert "alice%40example.com" in provisioning_uri  # email, URL-encoded by pyotp


async def test_enable_2fa_does_not_activate_until_verified(
    service: AuthService, verified_user: User, totp_repo: TOTPSecretRepository
) -> None:
    await service.enable_2fa(verified_user)

    record = await totp_repo.get_by_user_id(verified_user.id)
    assert record is not None
    assert record.enabled_at is None
    assert verified_user.totp_enabled is False


async def test_enable_2fa_already_enabled_raises_conflict(
    service: AuthService, verified_user: User
) -> None:
    await _enable_and_verify(service, verified_user)

    with pytest.raises(ConflictError):
        await service.enable_2fa(verified_user)


# --- verify_2fa --------------------------------------------------------------------


@pytest.mark.owasp("A07")
async def test_verify_2fa_happy_path_activates_and_returns_ten_backup_codes(
    service: AuthService, verified_user: User, user_repo: UserRepository
) -> None:
    _, secret = await service.enable_2fa(verified_user)

    backup_codes = await service.verify_2fa(verified_user, _current_code(secret))

    assert len(backup_codes) == 10
    assert len(set(backup_codes)) == 10  # all distinct
    refetched = await user_repo.get_by_id(verified_user.id)
    assert refetched is not None
    assert refetched.totp_enabled is True


async def test_verify_2fa_enqueues_security_notification_email(
    service: AuthService,
    verified_user: User,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    _, secret = await service.enable_2fa(verified_user)

    await service.verify_2fa(verified_user, _current_code(secret))

    email_jobs = [kwargs for name, kwargs in enqueued_jobs if name == "send_email"]
    assert len(email_jobs) == 1
    assert email_jobs[0]["template"] == "security_notification"
    assert email_jobs[0]["context"]["event"] == "totp_enabled"


async def test_verify_2fa_invalid_code_raises_authentication_error(
    service: AuthService, verified_user: User
) -> None:
    await service.enable_2fa(verified_user)

    with pytest.raises(AuthenticationError):
        await service.verify_2fa(verified_user, "000000")


async def test_verify_2fa_with_no_pending_setup_raises_conflict(
    service: AuthService, verified_user: User
) -> None:
    with pytest.raises(ConflictError):
        await service.verify_2fa(verified_user, "000000")


async def test_verify_2fa_already_enabled_raises_conflict(
    service: AuthService, verified_user: User
) -> None:
    secret, _ = await _enable_and_verify(service, verified_user)

    with pytest.raises(ConflictError):
        await service.verify_2fa(verified_user, _current_code(secret))


# --- disable_2fa -------------------------------------------------------------------


async def test_disable_2fa_happy_path(
    service: AuthService, verified_user: User, user_repo: UserRepository
) -> None:
    secret, _ = await _enable_and_verify(service, verified_user)

    await service.disable_2fa(verified_user, _VALID_PASSWORD, _next_code(secret))

    refetched = await user_repo.get_by_id(verified_user.id)
    assert refetched is not None
    assert refetched.totp_enabled is False


async def test_disable_2fa_enqueues_security_notification_email(
    service: AuthService,
    verified_user: User,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    secret, _ = await _enable_and_verify(service, verified_user)
    enqueued_jobs.clear()  # drop the totp_enabled notification from _enable_and_verify

    await service.disable_2fa(verified_user, _VALID_PASSWORD, _next_code(secret))

    email_jobs = [kwargs for name, kwargs in enqueued_jobs if name == "send_email"]
    assert len(email_jobs) == 1
    assert email_jobs[0]["template"] == "security_notification"
    assert email_jobs[0]["context"]["event"] == "totp_disabled"


async def test_disable_2fa_wrong_password_raises_authentication_error(
    service: AuthService, verified_user: User
) -> None:
    secret, _ = await _enable_and_verify(service, verified_user)

    with pytest.raises(AuthenticationError):
        await service.disable_2fa(verified_user, "wrong-password", _current_code(secret))


async def test_disable_2fa_wrong_code_raises_authentication_error(
    service: AuthService, verified_user: User
) -> None:
    await _enable_and_verify(service, verified_user)

    with pytest.raises(AuthenticationError):
        await service.disable_2fa(verified_user, _VALID_PASSWORD, "000000")


async def test_disable_2fa_when_not_enabled_raises_conflict(
    service: AuthService, verified_user: User
) -> None:
    with pytest.raises(ConflictError):
        await service.disable_2fa(verified_user, _VALID_PASSWORD, "000000")


async def test_disable_2fa_then_reenable_succeeds(
    service: AuthService, verified_user: User, user_repo: UserRepository
) -> None:
    secret, _ = await _enable_and_verify(service, verified_user)
    await service.disable_2fa(verified_user, _VALID_PASSWORD, _next_code(secret))

    # Re-enabling reuses TOTPSecret's single row (PK = user_id) rather than
    # hitting a unique-constraint conflict on a second insert.
    _, new_secret = await service.enable_2fa(verified_user)
    await service.verify_2fa(verified_user, _current_code(new_secret))

    refetched = await user_repo.get_by_id(verified_user.id)
    assert refetched is not None
    assert refetched.totp_enabled is True


# --- login() — modified flow (T-024) ------------------------------------------


async def test_login_without_2fa_returns_tokens_unchanged(
    service: AuthService, verified_user: User
) -> None:
    result = await service.login(
        LoginRequest(email=verified_user.email, password=_VALID_PASSWORD), _IP, _USER_AGENT
    )

    assert isinstance(result, tuple)
    access_token, refresh_plain, user = result
    assert access_token
    assert refresh_plain
    assert user.id == verified_user.id


async def test_login_with_2fa_enabled_returns_challenge_not_tokens(
    service: AuthService, verified_user: User
) -> None:
    await _enable_and_verify(service, verified_user)

    result = await service.login(
        LoginRequest(email=verified_user.email, password=_VALID_PASSWORD), _IP, _USER_AGENT
    )

    assert isinstance(result, TwoFactorChallenge)
    assert result.temp_token


# --- confirm_login_2fa -------------------------------------------------------------


async def test_confirm_login_2fa_with_totp_code_returns_tokens(
    service: AuthService, verified_user: User
) -> None:
    secret, _ = await _enable_and_verify(service, verified_user)
    challenge = await service.login(
        LoginRequest(email=verified_user.email, password=_VALID_PASSWORD), _IP, _USER_AGENT
    )
    assert isinstance(challenge, TwoFactorChallenge)

    access_token, refresh_plain, user = await service.confirm_login_2fa(
        challenge.temp_token, _next_code(secret), _IP, _USER_AGENT
    )

    assert access_token
    assert refresh_plain
    assert user.id == verified_user.id


async def test_confirm_login_2fa_invalid_code_raises(
    service: AuthService, verified_user: User
) -> None:
    await _enable_and_verify(service, verified_user)
    challenge = await service.login(
        LoginRequest(email=verified_user.email, password=_VALID_PASSWORD), _IP, _USER_AGENT
    )
    assert isinstance(challenge, TwoFactorChallenge)

    with pytest.raises(AuthenticationError):
        await service.confirm_login_2fa(challenge.temp_token, "000000", _IP, _USER_AGENT)


async def test_confirm_login_2fa_garbage_temp_token_raises(service: AuthService) -> None:
    with pytest.raises(AuthenticationError):
        await service.confirm_login_2fa("not-a-real-token", "000000", _IP, _USER_AGENT)


async def test_confirm_login_2fa_token_signed_for_different_purpose_raises(
    service: AuthService, verified_user: User
) -> None:
    other_purpose_token = sign_url_token(
        {"user_id": str(verified_user.id), "purpose": "password_reset"}, 300
    )

    with pytest.raises(AuthenticationError):
        await service.confirm_login_2fa(other_purpose_token, "000000", _IP, _USER_AGENT)


async def test_confirm_login_2fa_for_deleted_user_raises(
    service: AuthService, verified_user: User, session: AsyncSession
) -> None:
    await _enable_and_verify(service, verified_user)
    challenge = await service.login(
        LoginRequest(email=verified_user.email, password=_VALID_PASSWORD), _IP, _USER_AGENT
    )
    assert isinstance(challenge, TwoFactorChallenge)
    verified_user.deleted_at = datetime.now(UTC)
    await session.flush()

    with pytest.raises(AuthenticationError):
        await service.confirm_login_2fa(challenge.temp_token, "000000", _IP, _USER_AGENT)


async def test_confirm_login_2fa_after_2fa_disabled_raises(
    service: AuthService, verified_user: User
) -> None:
    secret, _ = await _enable_and_verify(service, verified_user)
    challenge = await service.login(
        LoginRequest(email=verified_user.email, password=_VALID_PASSWORD), _IP, _USER_AGENT
    )
    assert isinstance(challenge, TwoFactorChallenge)
    await service.disable_2fa(verified_user, _VALID_PASSWORD, _next_code(secret))

    with pytest.raises(AuthenticationError):
        await service.confirm_login_2fa(
            challenge.temp_token, _current_code(secret), _IP, _USER_AGENT
        )


async def test_confirm_login_2fa_with_backup_code_returns_tokens(
    service: AuthService, verified_user: User
) -> None:
    _, backup_codes = await _enable_and_verify(service, verified_user)
    challenge = await service.login(
        LoginRequest(email=verified_user.email, password=_VALID_PASSWORD), _IP, _USER_AGENT
    )
    assert isinstance(challenge, TwoFactorChallenge)

    access_token, refresh_plain, user = await service.confirm_login_2fa(
        challenge.temp_token, backup_codes[0], _IP, _USER_AGENT
    )

    assert access_token
    assert refresh_plain
    assert user.id == verified_user.id


async def test_backup_code_is_single_use(service: AuthService, verified_user: User) -> None:
    _, backup_codes = await _enable_and_verify(service, verified_user)
    used_code = backup_codes[0]

    async def _challenge() -> TwoFactorChallenge:
        result = await service.login(
            LoginRequest(email=verified_user.email, password=_VALID_PASSWORD), _IP, _USER_AGENT
        )
        assert isinstance(result, TwoFactorChallenge)
        return result

    first_challenge = await _challenge()
    await service.confirm_login_2fa(first_challenge.temp_token, used_code, _IP, _USER_AGENT)

    second_challenge = await _challenge()
    with pytest.raises(AuthenticationError):
        await service.confirm_login_2fa(second_challenge.temp_token, used_code, _IP, _USER_AGENT)


async def _challenge_for(service: AuthService, user: User) -> TwoFactorChallenge:
    result = await service.login(
        LoginRequest(email=user.email, password=_VALID_PASSWORD), _IP, _USER_AGENT
    )
    assert isinstance(result, TwoFactorChallenge)
    return result


async def _stored_backup_codes(service: AuthService, user: User) -> list[str]:
    record = await TOTPSecretRepository(service._db).get_by_user_id(user.id)
    assert record is not None
    stored: list[str] = json.loads(record.backup_codes_hash)
    return stored


async def test_backup_codes_are_stored_as_keyed_digests_not_argon2(
    service: AuthService, verified_user: User
) -> None:
    _, backup_codes = await _enable_and_verify(service, verified_user)

    stored = await _stored_backup_codes(service, verified_user)

    assert stored == [hash_backup_code(code) for code in backup_codes]
    assert not any(is_legacy_backup_code(hashed) for hashed in stored)


async def test_a_wrong_backup_code_costs_no_argon2(
    service: AuthService, verified_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Up to ten Argon2 verifications per wrong code, before: cheap for an
    attacker to request, expensive to answer."""
    await _enable_and_verify(service, verified_user)
    challenge = await _challenge_for(service, verified_user)

    def _no_argon2(plain: str, hashed: str) -> bool:  # noqa: ARG001 -- verify_password's signature
        raise AssertionError("Argon2 was run for a keyed backup code")

    monkeypatch.setattr(auth_service_module, "verify_password", _no_argon2)

    with pytest.raises(AuthenticationError):
        await service.confirm_login_2fa(challenge.temp_token, "0000000000", _IP, _USER_AGENT)


async def test_argon2_backup_codes_from_before_still_work_once(
    service: AuthService, verified_user: User
) -> None:
    """The plaintext of an existing code is gone, so it cannot be re-keyed:
    Argon2-hashed codes keep verifying until the user regenerates them."""
    await _enable_and_verify(service, verified_user)
    legacy_code = "abcde12345"
    await TOTPSecretRepository(service._db).update_backup_codes(
        verified_user.id, json.dumps([hash_password(legacy_code)])
    )
    await service._db.commit()

    first = await _challenge_for(service, verified_user)
    await service.confirm_login_2fa(first.temp_token, legacy_code, _IP, _USER_AGENT)
    second = await _challenge_for(service, verified_user)

    with pytest.raises(AuthenticationError):
        await service.confirm_login_2fa(second.temp_token, legacy_code, _IP, _USER_AGENT)


# --- TOTP same-window replay tracking (T-218/ASVS V2.8.4-V2.8.5) ----------------


async def test_totp_code_replayed_at_next_login_is_rejected(
    service: AuthService, verified_user: User
) -> None:
    """The exact code consumed by verify_2fa's own enable step is replayed
    at the very next login -- same 30s window, so it's still "valid" per
    pyotp's own verify(), but already spent."""
    _, secret = await service.enable_2fa(verified_user)
    code = _current_code(secret)
    await service.verify_2fa(verified_user, code)

    challenge = await service.login(
        LoginRequest(email=verified_user.email, password=_VALID_PASSWORD), _IP, _USER_AGENT
    )
    assert isinstance(challenge, TwoFactorChallenge)

    with pytest.raises(AuthenticationError):
        await service.confirm_login_2fa(challenge.temp_token, code, _IP, _USER_AGENT)


async def test_totp_code_replayed_within_disable_is_rejected(
    service: AuthService, verified_user: User
) -> None:
    secret, _ = await _enable_and_verify(service, verified_user)  # consumes the "enable" step
    code = _next_code(secret)  # a fresh, not-yet-consumed step
    # A legitimate first use (e.g. a fresh confirm_login_2fa) consumes this
    # step; disable_2fa then replaying the identical code is rejected too --
    # replay tracking is shared across every call site, not per-endpoint.
    challenge = await service.login(
        LoginRequest(email=verified_user.email, password=_VALID_PASSWORD), _IP, _USER_AGENT
    )
    assert isinstance(challenge, TwoFactorChallenge)
    await service.confirm_login_2fa(challenge.temp_token, code, _IP, _USER_AGENT)

    with pytest.raises(AuthenticationError):
        await service.disable_2fa(verified_user, _VALID_PASSWORD, code)


async def test_totp_replay_rejection_is_logged_and_notified(
    service: AuthService,
    verified_user: User,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    _, secret = await service.enable_2fa(verified_user)
    code = _current_code(secret)
    await service.verify_2fa(verified_user, code)
    enqueued_jobs.clear()  # drop the totp_enabled notification

    challenge = await service.login(
        LoginRequest(email=verified_user.email, password=_VALID_PASSWORD), _IP, _USER_AGENT
    )
    assert isinstance(challenge, TwoFactorChallenge)
    with pytest.raises(AuthenticationError):
        await service.confirm_login_2fa(challenge.temp_token, code, _IP, _USER_AGENT)

    email_jobs = [kwargs for name, kwargs in enqueued_jobs if name == "send_email"]
    assert len(email_jobs) == 1
    assert email_jobs[0]["template"] == "security_notification"
    assert email_jobs[0]["context"]["event"] == "totp_replay_rejected"


async def test_totp_replay_does_not_affect_a_different_users_secret(
    service: AuthService, verified_user: User, user_repo: UserRepository
) -> None:
    other_user = await user_repo.create(
        email="bob@example.com",
        hashed_password=hash_password(_VALID_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    secret_a, _ = await _enable_and_verify(service, verified_user)
    code_a = _next_code(secret_a)
    challenge_a = await service.login(
        LoginRequest(email=verified_user.email, password=_VALID_PASSWORD), _IP, _USER_AGENT
    )
    assert isinstance(challenge_a, TwoFactorChallenge)
    await service.confirm_login_2fa(challenge_a.temp_token, code_a, _IP, _USER_AGENT)

    # A fresh secret/user -- last_consumed_step lives on the TOTPSecret row,
    # so this must succeed even though verified_user's own replay of code_a
    # right now would be rejected.
    _, secret_b = await service.enable_2fa(other_user)
    backup_codes_b = await service.verify_2fa(other_user, _current_code(secret_b))
    assert len(backup_codes_b) == 10


# --- TOTPSecretRepository (direct) ----------------------------------------------


async def test_totp_repository_mark_enabled_unknown_user_raises_not_found(
    totp_repo: TOTPSecretRepository,
) -> None:
    with pytest.raises(NotFoundError):
        await totp_repo.mark_enabled(uuid4(), "[]")


# --- router-level fixtures (Postgres + Redis) -----------------------------------


def _auth_header(user: User) -> dict[str, str]:
    token = create_access_token(user.id, None, ())
    return {"Authorization": f"Bearer {token}"}


# --- POST /auth/2fa/enable ----------------------------------------------------


async def test_enable_endpoint_without_bearer_token_returns_401(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/auth/2fa/enable")

    assert response.status_code == 401


async def test_enable_endpoint_returns_provisioning_uri_and_secret(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    response = await client.post("/api/v1/auth/2fa/enable", headers=_auth_header(verified_user))

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["secret"]
    assert data["provisioning_uri"].startswith("otpauth://totp/")


# --- POST /auth/2fa/verify -----------------------------------------------------


async def test_verify_endpoint_happy_path_returns_backup_codes(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    headers = _auth_header(verified_user)
    enable_response = await client.post("/api/v1/auth/2fa/enable", headers=headers)
    secret = enable_response.json()["data"]["secret"]

    response = await client.post(
        "/api/v1/auth/2fa/verify", json={"code": _current_code(secret)}, headers=headers
    )

    assert response.status_code == 200
    assert len(response.json()["data"]["backup_codes"]) == 10


async def test_verify_endpoint_invalid_code_returns_401(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    headers = _auth_header(verified_user)
    await client.post("/api/v1/auth/2fa/enable", headers=headers)

    response = await client.post(
        "/api/v1/auth/2fa/verify", json={"code": "000000"}, headers=headers
    )

    assert response.status_code == 401


# --- POST /auth/2fa/disable ----------------------------------------------------


async def test_disable_endpoint_happy_path(client: httpx.AsyncClient, verified_user: User) -> None:
    headers = _auth_header(verified_user)
    enable_response = await client.post("/api/v1/auth/2fa/enable", headers=headers)
    secret = enable_response.json()["data"]["secret"]
    await client.post(
        "/api/v1/auth/2fa/verify", json={"code": _current_code(secret)}, headers=headers
    )

    response = await client.post(
        "/api/v1/auth/2fa/disable",
        json={"password": _VALID_PASSWORD, "code": _next_code(secret)},
        headers=headers,
    )

    assert response.status_code == 200


async def test_disable_endpoint_wrong_password_returns_401(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    headers = _auth_header(verified_user)
    enable_response = await client.post("/api/v1/auth/2fa/enable", headers=headers)
    secret = enable_response.json()["data"]["secret"]
    await client.post(
        "/api/v1/auth/2fa/verify", json={"code": _current_code(secret)}, headers=headers
    )

    response = await client.post(
        "/api/v1/auth/2fa/disable",
        json={"password": "wrong-password", "code": _current_code(secret)},
        headers=headers,
    )

    assert response.status_code == 401


# --- full /login -> /2fa/login round trip via HTTP -----------------------------


async def test_login_then_2fa_login_round_trip_sets_refresh_cookie(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    headers = _auth_header(verified_user)
    enable_response = await client.post("/api/v1/auth/2fa/enable", headers=headers)
    secret = enable_response.json()["data"]["secret"]
    await client.post(
        "/api/v1/auth/2fa/verify", json={"code": _current_code(secret)}, headers=headers
    )

    login_response = await client.post(
        "/api/v1/auth/login", json={"email": verified_user.email, "password": _VALID_PASSWORD}
    )
    assert login_response.status_code == 200
    assert login_response.json()["data"]["pending_2fa"] is True
    temp_token = login_response.json()["data"]["temp_token"]
    assert "set-cookie" not in login_response.headers

    response = await client.post(
        "/api/v1/auth/2fa/login", json={"temp_token": temp_token, "code": _next_code(secret)}
    )

    assert response.status_code == 200
    assert response.json()["data"]["access_token"]
    assert response.headers.get_list("set-cookie")


async def test_login_then_2fa_login_round_trip_returns_refresh_token_for_mobile(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    headers = _auth_header(verified_user)
    enable_response = await client.post("/api/v1/auth/2fa/enable", headers=headers)
    secret = enable_response.json()["data"]["secret"]
    await client.post(
        "/api/v1/auth/2fa/verify", json={"code": _current_code(secret)}, headers=headers
    )

    login_response = await client.post(
        "/api/v1/auth/login", json={"email": verified_user.email, "password": _VALID_PASSWORD}
    )
    temp_token = login_response.json()["data"]["temp_token"]

    response = await client.post(
        "/api/v1/auth/2fa/login",
        json={"temp_token": temp_token, "code": _next_code(secret)},
        headers={"X-Client-Platform": "mobile"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["refresh_token"]


async def test_2fa_login_endpoint_invalid_code_returns_401(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    headers = _auth_header(verified_user)
    enable_response = await client.post("/api/v1/auth/2fa/enable", headers=headers)
    secret = enable_response.json()["data"]["secret"]
    await client.post(
        "/api/v1/auth/2fa/verify", json={"code": _current_code(secret)}, headers=headers
    )
    login_response = await client.post(
        "/api/v1/auth/login", json={"email": verified_user.email, "password": _VALID_PASSWORD}
    )
    temp_token = login_response.json()["data"]["temp_token"]

    response = await client.post(
        "/api/v1/auth/2fa/login", json={"temp_token": temp_token, "code": "000000"}
    )

    assert response.status_code == 401


# --- review R3: what a session proved, and what enrolling does to the rest ---


async def _mobile_login(client: httpx.AsyncClient, user: User) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": _VALID_PASSWORD},
        headers={"X-Client-Platform": "mobile"},
    )
    assert response.status_code == 200, response.text
    client.cookies.clear()
    data: dict[str, Any] = response.json()["data"]
    return data


def _bearer(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


async def _session_amr(
    session_factory: async_sessionmaker[AsyncSession], access_token: str
) -> list[str]:
    sid = verify_access_token(access_token).sid
    assert sid is not None
    async with session_factory() as fresh:
        row = await fresh.get(SessionModel, sid)
        assert row is not None
        return list(row.amr)


@pytest.mark.owasp("A07")
async def test_enrolling_totp_signs_out_every_session_that_did_not_prove_it(
    client: httpx.AsyncClient,
    verified_user: User,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The review's two-session scenario.

    Device A signs in with a password. On device B the user turns on TOTP.
    A was opened below the bar the account now sets, so it has to go --
    before, it carried on and (for a superadmin) passed the admin gate.
    """
    device_a = await _mobile_login(client, verified_user)
    device_b = await _mobile_login(client, verified_user)
    assert await _session_amr(session_factory, device_a["access_token"]) == [amr.PASSWORD]

    b_headers = _bearer(device_b["access_token"])
    secret = (await client.post("/api/v1/auth/2fa/enable", headers=b_headers)).json()["data"][
        "secret"
    ]
    verified = await client.post(
        "/api/v1/auth/2fa/verify", json={"code": _current_code(secret)}, headers=b_headers
    )
    assert verified.status_code == 200, verified.text

    # A: signed out, both halves.
    a_me = await client.get("/api/v1/auth/me", headers=_bearer(device_a["access_token"]))
    assert a_me.status_code == 401
    a_refresh = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": device_a["refresh_token"]},
        headers={"X-Client-Platform": "mobile"},
    )
    assert a_refresh.status_code == 401

    # B: kept, and it now records the factor it just proved.
    assert (await client.get("/api/v1/auth/me", headers=b_headers)).status_code == 200
    assert await _session_amr(session_factory, device_b["access_token"]) == [
        amr.PASSWORD,
        amr.TOTP,
    ]


async def test_a_two_factor_login_records_both_methods_and_refresh_keeps_them(
    client: httpx.AsyncClient,
    verified_user: User,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    headers = _auth_header(verified_user)
    secret = (await client.post("/api/v1/auth/2fa/enable", headers=headers)).json()["data"][
        "secret"
    ]
    await client.post(
        "/api/v1/auth/2fa/verify", json={"code": _current_code(secret)}, headers=headers
    )

    challenge = await client.post(
        "/api/v1/auth/login", json={"email": verified_user.email, "password": _VALID_PASSWORD}
    )
    temp_token = challenge.json()["data"]["temp_token"]
    signed_in = await client.post(
        "/api/v1/auth/2fa/login",
        json={"temp_token": temp_token, "code": _next_code(secret)},
        headers={"X-Client-Platform": "mobile"},
    )
    assert signed_in.status_code == 200, signed_in.text
    tokens = signed_in.json()["data"]
    client.cookies.clear()
    assert await _session_amr(session_factory, tokens["access_token"]) == [amr.PASSWORD, amr.TOTP]

    rotated = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
        headers={"X-Client-Platform": "mobile"},
    )
    assert rotated.status_code == 200, rotated.text
    successor = rotated.json()["data"]["access_token"]
    assert await _session_amr(session_factory, successor) == [amr.PASSWORD, amr.TOTP]


@pytest.mark.owasp("A07")
@pytest.mark.module_dependent
async def test_an_enrolled_superadmin_on_a_password_only_session_cannot_reach_admin(
    client: httpx.AsyncClient,
    user_repo: UserRepository,
    session: AsyncSession,
) -> None:
    """The gate reads the session, not the account (review R3).

    The account is enrolled, so the old check passed. The session in hand
    never saw the second factor -- the shape of one opened before enrollment.
    """
    admin = await user_repo.create(
        email=f"admin-{uuid4()}@example.com",
        hashed_password=hash_password(_VALID_PASSWORD),
        email_verified_at=datetime.now(UTC),
        is_superadmin=True,
        totp_enabled=True,
    )
    await session.commit()
    password_only = await session_token(session, admin, methods=(amr.PASSWORD,))

    response = await client.get("/api/v1/admin/orgs", headers=_bearer(password_only))

    assert response.status_code == 403
    assert response.json()["error"]["message_key"] == "errors.superadmin_mfa_session_required"
