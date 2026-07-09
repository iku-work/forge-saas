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

from collections.abc import AsyncGenerator, Callable, Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
import pyotp
import pytest
from httpx import ASGITransport
from limits.storage import RedisStorage, storage_from_string
from limits.strategies import FixedWindowRateLimiter
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from app.core.db import get_session
from app.core.dependencies import get_redis
from app.core.exceptions import AuthenticationError, ConflictError, NotFoundError
from app.main import app
from app.middleware import rate_limit as rate_limit_module
from app.modules.auth.models import Session as SessionModel
from app.modules.auth.models import TOTPSecret, User
from app.modules.auth.repository import TOTPSecretRepository, UserRepository
from app.modules.auth.schemas import LoginRequest
from app.modules.auth.service import AuthService, TwoFactorChallenge
from app.modules.auth.utils import create_access_token, hash_password, sign_url_token

_TABLES = [User.__table__, SessionModel.__table__, TOTPSecret.__table__]
_VALID_PASSWORD = "Correct-Horse9"
_IP = "127.0.0.1"
_USER_AGENT = "pytest"


def _current_code(secret: str) -> str:
    return pyotp.TOTP(secret).now()


# --- service-level fixtures (Postgres only) -------------------------------------


@pytest.fixture(scope="module")
def postgres_container() -> "PostgresContainer":
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


# Function-scoped: an asyncpg-backed engine can't be reused across
# pytest-asyncio's per-test event loop (see test_auth_repository.py).
@pytest.fixture
async def test_engine(postgres_container: "PostgresContainer") -> AsyncGenerator[AsyncEngine, None]:
    url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all, tables=_TABLES)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all, tables=_TABLES)
    await engine.dispose()


@pytest.fixture
def session_factory(test_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as s:
        yield s


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

    await service.disable_2fa(verified_user, _VALID_PASSWORD, _current_code(secret))

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

    await service.disable_2fa(verified_user, _VALID_PASSWORD, _current_code(secret))

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
    await service.disable_2fa(verified_user, _VALID_PASSWORD, _current_code(secret))

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
        challenge.temp_token, _current_code(secret), _IP, _USER_AGENT
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
    await service.disable_2fa(verified_user, _VALID_PASSWORD, _current_code(secret))

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


# --- TOTPSecretRepository (direct) ----------------------------------------------


async def test_totp_repository_mark_enabled_unknown_user_raises_not_found(
    totp_repo: TOTPSecretRepository,
) -> None:
    with pytest.raises(NotFoundError):
        await totp_repo.mark_enabled(uuid4(), "[]")


# --- router-level fixtures (Postgres + Redis) -----------------------------------


@pytest.fixture(scope="module")
def redis_container() -> "RedisContainer":
    with RedisContainer("redis:7-alpine") as container:
        yield container


@pytest.fixture
def get_session_override(
    session_factory: async_sessionmaker[AsyncSession],
) -> Callable[[], AsyncGenerator[AsyncSession, None]]:
    # Mirrors app.core.db.get_session: a fresh session per request, so a
    # route's `await session.commit()` is what makes the write visible to
    # the next request -- the same contract as production.
    async def _get_session() -> AsyncGenerator[AsyncSession, None]:
        request_session = session_factory()
        try:
            yield request_session
        except Exception:
            await request_session.rollback()
            raise
        finally:
            await request_session.close()

    return _get_session


@pytest.fixture
async def redis_client(redis_container: "RedisContainer") -> AsyncGenerator[Redis, None]:
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    client: Redis = Redis.from_url(f"redis://{host}:{port}/0")
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture(scope="module")
def _redis_backed_storage(redis_container: "RedisContainer") -> Iterator[RedisStorage]:
    # Same swap-the-singleton's-storage approach as test_auth_router.py:
    # the 2fa endpoints below are all @auth_limit/@api_limit/@sensitive_limit.
    original_storage = rate_limit_module.limiter._storage
    original_limiter = rate_limit_module.limiter._limiter
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    storage = storage_from_string(f"redis://{host}:{port}/1")
    rate_limit_module.limiter._storage = storage
    rate_limit_module.limiter._limiter = FixedWindowRateLimiter(storage)
    yield storage
    rate_limit_module.limiter._storage = original_storage
    rate_limit_module.limiter._limiter = original_limiter


@pytest.fixture
async def client(
    _redis_backed_storage: RedisStorage,
    get_session_override: Callable[[], AsyncGenerator[AsyncSession, None]],
    redis_client: Redis,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    _redis_backed_storage.reset()
    app.dependency_overrides[get_session] = get_session_override
    app.dependency_overrides[get_redis] = lambda: redis_client
    try:
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as c:
                yield c
    finally:
        app.dependency_overrides.clear()


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
        json={"password": _VALID_PASSWORD, "code": _current_code(secret)},
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
        "/api/v1/auth/2fa/login", json={"temp_token": temp_token, "code": _current_code(secret)}
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
        json={"temp_token": temp_token, "code": _current_code(secret)},
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
