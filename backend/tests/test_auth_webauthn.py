"""
Verifies T-217 (WebAuthn/FIDO2 second factor): registration and assertion
ceremonies round-tripped against a software (virtual) authenticator built
from a plain EC P-256 key pair -- no real hardware key or browser is
available in CI, so `SoftAuthenticator` below plays that role, constructing
the exact CBOR/COSE structures `py_webauthn`'s verify_registration_response/
verify_authentication_response expect (see backend/app/modules/auth/
service.py's begin_webauthn_registration/complete_webauthn_registration/
begin_webauthn_login/complete_webauthn_login).

Uses the same real-Postgres (+ real-Redis for the router-level tests)
testcontainers pattern as test_auth_totp.py.
"""

import hashlib
import json
import secrets
from collections.abc import AsyncGenerator, Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import cbor2
import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
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
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

from app.core.config import get_settings
from app.core.db import get_session
from app.core.dependencies import get_redis
from app.core.exceptions import AuthenticationError, NotFoundError
from app.main import app
from app.middleware import rate_limit as rate_limit_module
from app.modules.auth.models import Session as SessionModel
from app.modules.auth.models import User, WebAuthnCredential
from app.modules.auth.repository import UserRepository, WebAuthnCredentialRepository
from app.modules.auth.schemas import LoginRequest
from app.modules.auth.service import AuthService, TwoFactorChallenge
from app.modules.auth.utils import create_access_token, hash_password, sign_url_token
from tests._module_tables import with_module_hot_path_tables

# Wrapped rather than listed: an enabled optional module reaches for tables
# this file has no other reason to know about. See tests/_module_tables.py.
_TABLES = with_module_hot_path_tables(
    User.__table__,
    SessionModel.__table__,
    WebAuthnCredential.__table__,
)
_VALID_PASSWORD = "Correct-Horse9"
_IP = "127.0.0.1"
_USER_AGENT = "pytest"


class SoftAuthenticator:
    """A software authenticator: one EC P-256 key pair, playing the same
    role a real security key or platform authenticator would in the
    navigator.credentials.create()/get() ceremonies."""

    def __init__(self) -> None:
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = secrets.token_bytes(32)
        self.sign_count = 0
        numbers = self.private_key.public_key().public_numbers()
        self._x = numbers.x.to_bytes(32, "big")
        self._y = numbers.y.to_bytes(32, "big")

    def _cose_public_key(self) -> bytes:
        # kty=EC2(2), alg=ES256(-7), crv=P-256(1) -- see
        # webauthn.helpers.cose.COSEKey/COSEKTY/COSECRV/COSEAlgorithmIdentifier.
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: self._x, -3: self._y})

    @staticmethod
    def _rp_id_hash(rp_id: str) -> bytes:
        return hashlib.sha256(rp_id.encode("utf-8")).digest()

    @staticmethod
    def _client_data(*, ceremony_type: str, challenge: bytes, origin: str) -> bytes:
        return json.dumps(
            {
                "type": ceremony_type,
                "challenge": bytes_to_base64url(challenge),
                "origin": origin,
            },
            separators=(",", ":"),
        ).encode("utf-8")

    def create(self, *, rp_id: str, origin: str, challenge: bytes) -> dict[str, Any]:
        """navigator.credentials.create() -- registration, "none" attestation."""
        client_data = self._client_data(
            ceremony_type="webauthn.create", challenge=challenge, origin=origin
        )
        flags = 0x41  # bit 0 (UP) + bit 6 (AT)
        auth_data = (
            self._rp_id_hash(rp_id)
            + bytes([flags])
            + self.sign_count.to_bytes(4, "big")
            + bytes(16)  # aaguid — all-zero for this software authenticator
            + len(self.credential_id).to_bytes(2, "big")
            + self.credential_id
            + self._cose_public_key()
        )
        attestation_object = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": bytes_to_base64url(self.credential_id),
            "rawId": bytes_to_base64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "attestationObject": bytes_to_base64url(attestation_object),
            },
        }

    def get(
        self, *, rp_id: str, origin: str, challenge: bytes, sign_count: int | None = None
    ) -> dict[str, Any]:
        """navigator.credentials.get() -- assertion. `sign_count` overrides the
        authenticator's own increment-by-one behavior, for exercising replay/
        counter-regression detection."""
        count = self.sign_count + 1 if sign_count is None else sign_count
        client_data = self._client_data(
            ceremony_type="webauthn.get", challenge=challenge, origin=origin
        )
        flags = 0x01  # bit 0 (UP) only -- no attested credential data on assertion
        auth_data = self._rp_id_hash(rp_id) + bytes([flags]) + count.to_bytes(4, "big")
        client_data_hash = hashlib.sha256(client_data).digest()
        signature = self.private_key.sign(auth_data + client_data_hash, ec.ECDSA(hashes.SHA256()))
        self.sign_count = count
        return {
            "id": bytes_to_base64url(self.credential_id),
            "rawId": bytes_to_base64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "authenticatorData": bytes_to_base64url(auth_data),
                "signature": bytes_to_base64url(signature),
            },
        }


def _challenge_from(options: dict[str, Any]) -> bytes:
    return base64url_to_bytes(options["challenge"])


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
def webauthn_repo(session: AsyncSession) -> WebAuthnCredentialRepository:
    return WebAuthnCredentialRepository(session)


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


async def _register(
    service: AuthService, user: User
) -> tuple[SoftAuthenticator, WebAuthnCredential]:
    """Drives begin_webauthn_registration -> complete_webauthn_registration to a
    fully-registered credential and hands back (authenticator, record)."""
    settings = get_settings()
    options, challenge_token = await service.begin_webauthn_registration(user)
    authenticator = SoftAuthenticator()
    credential = authenticator.create(
        rp_id=settings.DOMAIN, origin=settings.FRONTEND_URL, challenge=_challenge_from(options)
    )
    record = await service.complete_webauthn_registration(
        user, challenge_token, credential, "My key"
    )
    return authenticator, record


# --- begin_webauthn_registration / complete_webauthn_registration --------------


async def test_begin_registration_returns_options_and_challenge_token(
    service: AuthService, verified_user: User
) -> None:
    options, challenge_token = await service.begin_webauthn_registration(verified_user)

    assert options["rp"]["id"] == get_settings().DOMAIN
    assert options["user"]["name"] == verified_user.email
    assert options["challenge"]
    assert challenge_token


async def test_complete_registration_happy_path_creates_credential(
    service: AuthService, verified_user: User, webauthn_repo: WebAuthnCredentialRepository
) -> None:
    _, record = await _register(service, verified_user)

    assert record.nickname == "My key"
    assert record.sign_count == 0
    refetched = await webauthn_repo.get_by_id(record.id)
    assert refetched is not None
    assert refetched.user_id == verified_user.id


async def test_complete_registration_enqueues_security_notification_email(
    service: AuthService,
    verified_user: User,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    await _register(service, verified_user)

    email_jobs = [kwargs for name, kwargs in enqueued_jobs if name == "send_email"]
    assert len(email_jobs) == 1
    assert email_jobs[0]["template"] == "security_notification"
    assert email_jobs[0]["context"]["event"] == "webauthn_registered"


async def test_complete_registration_with_garbage_challenge_token_raises(
    service: AuthService, verified_user: User
) -> None:
    authenticator = SoftAuthenticator()
    settings = get_settings()
    credential = authenticator.create(
        rp_id=settings.DOMAIN, origin=settings.FRONTEND_URL, challenge=secrets.token_bytes(64)
    )

    with pytest.raises(AuthenticationError):
        await service.complete_webauthn_registration(
            verified_user, "not-a-real-token", credential, None
        )


async def test_complete_registration_token_for_different_user_raises(
    service: AuthService, verified_user: User, user_repo: UserRepository, session: AsyncSession
) -> None:
    other_user = await user_repo.create(
        email="mallory@example.com",
        hashed_password=hash_password(_VALID_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    await session.commit()
    options, challenge_token = await service.begin_webauthn_registration(other_user)
    settings = get_settings()
    authenticator = SoftAuthenticator()
    credential = authenticator.create(
        rp_id=settings.DOMAIN, origin=settings.FRONTEND_URL, challenge=_challenge_from(options)
    )

    with pytest.raises(AuthenticationError):
        await service.complete_webauthn_registration(
            verified_user, challenge_token, credential, None
        )


async def test_complete_registration_tampered_signature_raises(
    service: AuthService, verified_user: User
) -> None:
    settings = get_settings()
    options, challenge_token = await service.begin_webauthn_registration(verified_user)
    authenticator = SoftAuthenticator()
    credential = authenticator.create(
        rp_id=settings.DOMAIN, origin=settings.FRONTEND_URL, challenge=_challenge_from(options)
    )
    # Corrupt the attestation object so it no longer parses as valid CBOR/
    # authenticator data -- simulates a forged/corrupted response.
    credential["response"]["attestationObject"] = bytes_to_base64url(b"not-a-real-attestation")

    with pytest.raises(AuthenticationError):
        await service.complete_webauthn_registration(
            verified_user, challenge_token, credential, None
        )


# --- list / revoke -----------------------------------------------------------


async def test_list_webauthn_credentials_returns_registered(
    service: AuthService, verified_user: User
) -> None:
    await _register(service, verified_user)

    credentials = await service.list_webauthn_credentials(verified_user.id)

    assert len(credentials) == 1


async def test_revoke_webauthn_credential_happy_path(
    service: AuthService, verified_user: User, webauthn_repo: WebAuthnCredentialRepository
) -> None:
    _, record = await _register(service, verified_user)

    await service.revoke_webauthn_credential(verified_user.id, record.id)

    assert await webauthn_repo.get_by_id(record.id) is None


async def test_revoke_webauthn_credential_enqueues_security_notification_email(
    service: AuthService,
    verified_user: User,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    _, record = await _register(service, verified_user)
    enqueued_jobs.clear()  # drop the webauthn_registered notification from _register

    await service.revoke_webauthn_credential(verified_user.id, record.id)

    email_jobs = [kwargs for name, kwargs in enqueued_jobs if name == "send_email"]
    assert len(email_jobs) == 1
    assert email_jobs[0]["context"]["event"] == "webauthn_removed"


async def test_revoke_webauthn_credential_belonging_to_another_user_raises(
    service: AuthService,
    verified_user: User,
    user_repo: UserRepository,
    session: AsyncSession,
) -> None:
    _, record = await _register(service, verified_user)
    other_user = await user_repo.create(
        email="mallory2@example.com",
        hashed_password=hash_password(_VALID_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    await session.commit()

    with pytest.raises(NotFoundError):
        await service.revoke_webauthn_credential(other_user.id, record.id)


# --- login() gate — WebAuthn as an alternative to TOTP (T-217) -----------------


async def test_login_with_registered_webauthn_credential_returns_challenge(
    service: AuthService, verified_user: User
) -> None:
    await _register(service, verified_user)

    result = await service.login(
        LoginRequest(email=verified_user.email, password=_VALID_PASSWORD), _IP, _USER_AGENT
    )

    assert isinstance(result, TwoFactorChallenge)


async def test_login_without_2fa_of_any_kind_returns_tokens(
    service: AuthService, verified_user: User
) -> None:
    result = await service.login(
        LoginRequest(email=verified_user.email, password=_VALID_PASSWORD), _IP, _USER_AGENT
    )

    assert isinstance(result, tuple)


# --- begin_webauthn_login / complete_webauthn_login ----------------------------


async def test_begin_webauthn_login_with_totp_purpose_token_wrong_returns_options(
    service: AuthService, verified_user: User
) -> None:
    authenticator, _ = await _register(service, verified_user)
    challenge = await service.login(
        LoginRequest(email=verified_user.email, password=_VALID_PASSWORD), _IP, _USER_AGENT
    )
    assert isinstance(challenge, TwoFactorChallenge)

    options, login_challenge_token = await service.begin_webauthn_login(challenge.temp_token)

    assert options["allowCredentials"][0]["id"] == bytes_to_base64url(authenticator.credential_id)
    assert login_challenge_token


async def test_begin_webauthn_login_with_no_registered_credentials_raises(
    service: AuthService, verified_user: User
) -> None:
    challenge = await service.login(
        LoginRequest(email=verified_user.email, password=_VALID_PASSWORD), _IP, _USER_AGENT
    )
    # verified_user has no 2FA at all here, so login() returns tokens, not a
    # challenge -- forge one anyway to prove begin_webauthn_login rejects it.
    assert isinstance(challenge, tuple)
    temp_token = sign_url_token({"user_id": str(verified_user.id), "purpose": "2fa_login"}, 300)

    with pytest.raises(AuthenticationError):
        await service.begin_webauthn_login(temp_token)


async def test_begin_webauthn_login_garbage_temp_token_raises(service: AuthService) -> None:
    with pytest.raises(AuthenticationError):
        await service.begin_webauthn_login("not-a-real-token")


async def test_complete_webauthn_login_happy_path_returns_tokens_and_bumps_sign_count(
    service: AuthService,
    verified_user: User,
    webauthn_repo: WebAuthnCredentialRepository,
) -> None:
    authenticator, record = await _register(service, verified_user)
    challenge = await service.login(
        LoginRequest(email=verified_user.email, password=_VALID_PASSWORD), _IP, _USER_AGENT
    )
    assert isinstance(challenge, TwoFactorChallenge)
    options, login_challenge_token = await service.begin_webauthn_login(challenge.temp_token)
    settings = get_settings()
    assertion = authenticator.get(
        rp_id=settings.DOMAIN, origin=settings.FRONTEND_URL, challenge=_challenge_from(options)
    )

    access_token, refresh_plain, user = await service.complete_webauthn_login(
        login_challenge_token, assertion, _IP, _USER_AGENT
    )

    assert access_token
    assert refresh_plain
    assert user.id == verified_user.id
    refetched = await webauthn_repo.get_by_id(record.id)
    assert refetched is not None
    assert refetched.sign_count == 1
    assert refetched.last_used_at is not None


async def test_complete_webauthn_login_garbage_challenge_token_raises(
    service: AuthService,
) -> None:
    with pytest.raises(AuthenticationError):
        await service.complete_webauthn_login("not-a-real-token", {}, _IP, _USER_AGENT)


async def test_complete_webauthn_login_tampered_signature_raises(
    service: AuthService, verified_user: User
) -> None:
    authenticator, _ = await _register(service, verified_user)
    challenge = await service.login(
        LoginRequest(email=verified_user.email, password=_VALID_PASSWORD), _IP, _USER_AGENT
    )
    assert isinstance(challenge, TwoFactorChallenge)
    options, login_challenge_token = await service.begin_webauthn_login(challenge.temp_token)
    settings = get_settings()
    assertion = authenticator.get(
        rp_id=settings.DOMAIN, origin=settings.FRONTEND_URL, challenge=_challenge_from(options)
    )
    assertion["response"]["signature"] = bytes_to_base64url(b"forged-signature-bytes")

    with pytest.raises(AuthenticationError):
        await service.complete_webauthn_login(login_challenge_token, assertion, _IP, _USER_AGENT)


@pytest.mark.owasp("A07")
async def test_complete_webauthn_login_sign_count_regression_revokes_all_sessions(
    service: AuthService,
    verified_user: User,
    webauthn_repo: WebAuthnCredentialRepository,
) -> None:
    """A cloned credential replaying an old (or non-incrementing) counter is
    rejected, and -- same spirit as refresh-token reuse detection (T-019) --
    every one of the user's active sessions gets revoked, not just this
    attempt."""
    authenticator, record = await _register(service, verified_user)
    settings = get_settings()

    async def _do_webauthn_login() -> tuple[str, str, User]:
        challenge = await service.login(
            LoginRequest(email=verified_user.email, password=_VALID_PASSWORD), _IP, _USER_AGENT
        )
        assert isinstance(challenge, TwoFactorChallenge)
        options, login_challenge_token = await service.begin_webauthn_login(challenge.temp_token)
        assertion = authenticator.get(
            rp_id=settings.DOMAIN, origin=settings.FRONTEND_URL, challenge=_challenge_from(options)
        )
        return await service.complete_webauthn_login(
            login_challenge_token, assertion, _IP, _USER_AGENT
        )

    await _do_webauthn_login()  # sign_count: 0 -> 1
    active_after_first_login = await service.list_sessions(verified_user.id)
    assert len(active_after_first_login) == 1

    # Force a second assertion whose counter doesn't advance past what's
    # already stored -- simulates a cloned authenticator replaying a stale
    # counter value.
    challenge = await service.login(
        LoginRequest(email=verified_user.email, password=_VALID_PASSWORD), _IP, _USER_AGENT
    )
    assert isinstance(challenge, TwoFactorChallenge)
    options, login_challenge_token = await service.begin_webauthn_login(challenge.temp_token)
    replayed_assertion = authenticator.get(
        rp_id=settings.DOMAIN,
        origin=settings.FRONTEND_URL,
        challenge=_challenge_from(options),
        sign_count=1,  # same as the last stored count -- not an increment
    )

    with pytest.raises(AuthenticationError):
        await service.complete_webauthn_login(
            login_challenge_token, replayed_assertion, _IP, _USER_AGENT
        )

    active_after_regression = await service.list_sessions(verified_user.id)
    assert active_after_regression == []
    refetched = await webauthn_repo.get_by_id(record.id)
    assert refetched is not None
    assert refetched.sign_count == 1  # unchanged -- the bad assertion was rejected


# --- router-level fixtures (Postgres + Redis) -----------------------------------


@pytest.fixture(scope="module")
def redis_container() -> "RedisContainer":
    with RedisContainer("redis:7-alpine") as container:
        yield container


@pytest.fixture
def get_session_override(
    session_factory: async_sessionmaker[AsyncSession],
) -> Callable[[], AsyncGenerator[AsyncSession, None]]:
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


async def _register_via_http(
    client: httpx.AsyncClient, user: User
) -> tuple[SoftAuthenticator, dict[str, Any]]:
    headers = _auth_header(user)
    options_response = await client.post("/api/v1/auth/webauthn/register/options", headers=headers)
    options_data = options_response.json()["data"]
    settings = get_settings()
    authenticator = SoftAuthenticator()
    credential = authenticator.create(
        rp_id=settings.DOMAIN,
        origin=settings.FRONTEND_URL,
        challenge=_challenge_from(options_data["options"]),
    )
    register_response = await client.post(
        "/api/v1/auth/webauthn/register",
        json={
            "challenge_token": options_data["challenge_token"],
            "credential": credential,
            "nickname": "YubiKey",
        },
        headers=headers,
    )
    return authenticator, register_response.json()["data"]


# --- POST /auth/webauthn/register/options + /register --------------------------


async def test_register_options_endpoint_without_bearer_token_returns_401(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/api/v1/auth/webauthn/register/options")

    assert response.status_code == 401


async def test_register_endpoint_happy_path_returns_credential(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    _, credential_public = await _register_via_http(client, verified_user)

    assert credential_public["nickname"] == "YubiKey"
    assert credential_public["id"]


# --- GET /auth/webauthn/credentials + DELETE /credentials/{id} -----------------


async def test_list_credentials_endpoint_returns_registered_key(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    await _register_via_http(client, verified_user)

    response = await client.get(
        "/api/v1/auth/webauthn/credentials", headers=_auth_header(verified_user)
    )

    assert response.status_code == 200
    assert len(response.json()["data"]) == 1


async def test_revoke_credential_endpoint_removes_it(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    _, credential_public = await _register_via_http(client, verified_user)
    headers = _auth_header(verified_user)

    response = await client.delete(
        f"/api/v1/auth/webauthn/credentials/{credential_public['id']}", headers=headers
    )
    list_response = await client.get("/api/v1/auth/webauthn/credentials", headers=headers)

    assert response.status_code == 200
    assert list_response.json()["data"] == []


# --- full /login -> /webauthn/login round trip via HTTP -------------------------


async def test_login_then_webauthn_login_round_trip_sets_refresh_cookie(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    authenticator, _ = await _register_via_http(client, verified_user)

    login_response = await client.post(
        "/api/v1/auth/login", json={"email": verified_user.email, "password": _VALID_PASSWORD}
    )
    assert login_response.json()["data"]["pending_2fa"] is True
    temp_token = login_response.json()["data"]["temp_token"]
    assert "set-cookie" not in login_response.headers

    options_response = await client.post(
        "/api/v1/auth/webauthn/login/options", json={"temp_token": temp_token}
    )
    options_data = options_response.json()["data"]
    settings = get_settings()
    assertion = authenticator.get(
        rp_id=settings.DOMAIN,
        origin=settings.FRONTEND_URL,
        challenge=_challenge_from(options_data["options"]),
    )

    response = await client.post(
        "/api/v1/auth/webauthn/login",
        json={"challenge_token": options_data["challenge_token"], "credential": assertion},
    )

    assert response.status_code == 200
    assert response.json()["data"]["access_token"]
    assert response.headers.get_list("set-cookie")


async def test_webauthn_login_endpoint_tampered_signature_returns_401(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    authenticator, _ = await _register_via_http(client, verified_user)
    login_response = await client.post(
        "/api/v1/auth/login", json={"email": verified_user.email, "password": _VALID_PASSWORD}
    )
    temp_token = login_response.json()["data"]["temp_token"]
    options_response = await client.post(
        "/api/v1/auth/webauthn/login/options", json={"temp_token": temp_token}
    )
    options_data = options_response.json()["data"]
    settings = get_settings()
    assertion = authenticator.get(
        rp_id=settings.DOMAIN,
        origin=settings.FRONTEND_URL,
        challenge=_challenge_from(options_data["options"]),
    )
    assertion["response"]["signature"] = bytes_to_base64url(b"forged-signature-bytes")

    response = await client.post(
        "/api/v1/auth/webauthn/login",
        json={"challenge_token": options_data["challenge_token"], "credential": assertion},
    )

    assert response.status_code == 401
