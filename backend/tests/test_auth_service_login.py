"""
Verifies T-018 (auth service — login + lockout): constant-time handling of
unknown emails, the locked/unverified short-circuits, brute-force lockout
after 10 failed attempts, and session creation on success. Uses the same
real-Postgres testcontainers pattern as test_auth_service.py.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

from app.core.config import get_settings
from app.core.exceptions import AuthenticationError
from app.modules.audit.models import AuditEvent
from app.modules.auth.models import Session as SessionModel
from app.modules.auth.models import User, WebAuthnCredential
from app.modules.auth.repository import SessionRepository, UserRepository
from app.modules.auth.schemas import LoginRequest
from app.modules.auth.service import AuthService
from app.modules.auth.utils import hash_password, verify_password
from tests._database import DatabaseHandle
from tests._module_tables import with_module_hot_path_tables

# Wrapped rather than listed: an enabled optional module reaches for tables
# this file has no other reason to know about. See tests/_module_tables.py.
_TABLES = with_module_hot_path_tables(
    User.__table__,
    SessionModel.__table__,
    AuditEvent.__table__,
    WebAuthnCredential.__table__,
)
_VALID_PASSWORD = "Correct-Horse9"
_IP = "127.0.0.1"
_USER_AGENT = "pytest"


# Function-scoped (not module-scoped): see test_auth_repository.py — an
# asyncpg-backed engine can't be reused across pytest-asyncio's per-test loop.
@pytest.fixture
async def test_engine(postgres_container: DatabaseHandle) -> AsyncGenerator[AsyncEngine, None]:
    url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all, tables=_TABLES)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all, tables=_TABLES)
    await engine.dispose()


@pytest.fixture
async def session(test_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s


@pytest.fixture
def service(session: AsyncSession) -> AuthService:
    return AuthService(session)


@pytest.fixture
def user_repo(session: AsyncSession) -> UserRepository:
    return UserRepository(session)


@pytest.fixture
def session_repo(session: AsyncSession) -> SessionRepository:
    return SessionRepository(session)


@pytest.fixture
async def verified_user(user_repo: UserRepository) -> User:
    return await user_repo.create(
        email="alice@example.com",
        hashed_password=hash_password(_VALID_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )


# --- happy path ----------------------------------------------------------------


async def test_login_happy_path_returns_tokens_and_user(
    service: AuthService, verified_user: User
) -> None:
    access_token, refresh_plain, user = await service.login(
        LoginRequest(email="alice@example.com", password=_VALID_PASSWORD), _IP, _USER_AGENT
    )

    assert access_token
    assert refresh_plain
    assert user.id == verified_user.id


async def test_login_happy_path_creates_session(
    service: AuthService, verified_user: User, session_repo: SessionRepository
) -> None:
    await service.login(
        LoginRequest(email="alice@example.com", password=_VALID_PASSWORD), _IP, _USER_AGENT
    )

    active = await session_repo.list_active_for_user(verified_user.id)
    assert len(active) == 1
    assert active[0].ip_address == _IP
    assert active[0].user_agent == _USER_AGENT


async def test_login_happy_path_writes_audit_event(
    service: AuthService, verified_user: User, session: AsyncSession
) -> None:
    await service.login(
        LoginRequest(email="alice@example.com", password=_VALID_PASSWORD), _IP, _USER_AGENT
    )

    events = (
        (await session.execute(select(AuditEvent).where(AuditEvent.action == "user.login_success")))
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].actor_id == verified_user.id
    assert events[0].ip_address == _IP
    assert events[0].user_agent == _USER_AGENT
    assert events[0].org_id is None


async def test_login_happy_path_resets_failed_count(
    service: AuthService, verified_user: User, user_repo: UserRepository
) -> None:
    await user_repo.increment_failed_login(verified_user.id)

    await service.login(
        LoginRequest(email="alice@example.com", password=_VALID_PASSWORD), _IP, _USER_AGENT
    )

    fetched = await user_repo.get_by_id(verified_user.id)
    assert fetched is not None
    assert fetched.failed_login_count == 0


# --- unknown email ---------------------------------------------------------------


async def test_login_unknown_email_raises(service: AuthService) -> None:
    with pytest.raises(AuthenticationError):
        await service.login(
            LoginRequest(email="nobody@example.com", password=_VALID_PASSWORD), _IP, _USER_AGENT
        )


# --- lockout ---------------------------------------------------------------


async def test_login_wrong_password_increments_failed_count(
    service: AuthService, verified_user: User, user_repo: UserRepository
) -> None:
    with pytest.raises(AuthenticationError):
        await service.login(
            LoginRequest(email="alice@example.com", password="wrong-password"), _IP, _USER_AGENT
        )

    fetched = await user_repo.get_by_id(verified_user.id)
    assert fetched is not None
    assert fetched.failed_login_count == 1


@pytest.mark.owasp("A07")
async def test_login_wrong_password_writes_audit_event(
    service: AuthService, verified_user: User, session: AsyncSession
) -> None:
    with pytest.raises(AuthenticationError):
        await service.login(
            LoginRequest(email="alice@example.com", password="wrong-password"), _IP, _USER_AGENT
        )

    events = (
        (await session.execute(select(AuditEvent).where(AuditEvent.action == "user.login_failed")))
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].actor_id == verified_user.id
    assert events[0].ip_address == _IP
    assert events[0].user_agent == _USER_AGENT
    assert events[0].severity == "info"


@pytest.mark.owasp("A04")
@pytest.mark.owasp("A07")
async def test_login_tenth_wrong_password_triggers_lockout(
    service: AuthService, verified_user: User, user_repo: UserRepository
) -> None:
    for _ in range(10):
        with pytest.raises(AuthenticationError):
            await service.login(
                LoginRequest(email="alice@example.com", password="wrong-password"),
                _IP,
                _USER_AGENT,
            )

    fetched = await user_repo.get_by_id(verified_user.id)
    assert fetched is not None
    assert fetched.locked_until is not None
    assert fetched.locked_until > datetime.now(UTC)
    # Lockout resets the counter so the next window starts clean.
    assert fetched.failed_login_count == 0


@pytest.mark.owasp("A04")
@pytest.mark.owasp("A07")
async def test_login_tenth_wrong_password_writes_elevated_account_locked_event(
    service: AuthService, verified_user: User, session: AsyncSession
) -> None:
    for _ in range(10):
        with pytest.raises(AuthenticationError):
            await service.login(
                LoginRequest(email="alice@example.com", password="wrong-password"),
                _IP,
                _USER_AGENT,
            )

    locked_events = (
        (
            await session.execute(
                select(AuditEvent).where(AuditEvent.action == "user.account_locked")
            )
        )
        .scalars()
        .all()
    )
    assert len(locked_events) == 1
    assert locked_events[0].actor_id == verified_user.id
    assert locked_events[0].severity == "elevated"
    # The 9 attempts before the lockout-triggering 10th are each a plain
    # login_failed event -- account_locked doesn't replace them.
    failed_events = (
        (await session.execute(select(AuditEvent).where(AuditEvent.action == "user.login_failed")))
        .scalars()
        .all()
    )
    assert len(failed_events) == 9


@pytest.mark.owasp("A04")
@pytest.mark.owasp("A07")
async def test_login_locked_account_rejects_correct_password(
    service: AuthService, verified_user: User, user_repo: UserRepository
) -> None:
    await user_repo.set_lockout(verified_user.id, datetime.now(UTC) + timedelta(minutes=15))

    with pytest.raises(AuthenticationError):
        await service.login(
            LoginRequest(email="alice@example.com", password=_VALID_PASSWORD), _IP, _USER_AGENT
        )


async def test_login_expired_lockout_allows_correct_password(
    service: AuthService, verified_user: User, user_repo: UserRepository
) -> None:
    await user_repo.set_lockout(verified_user.id, datetime.now(UTC) - timedelta(minutes=1))

    access_token, refresh_plain, user = await service.login(
        LoginRequest(email="alice@example.com", password=_VALID_PASSWORD), _IP, _USER_AGENT
    )

    assert access_token
    assert refresh_plain
    assert user.id == verified_user.id


# --- unverified email ---------------------------------------------------------------


async def test_login_unverified_email_rejected(
    service: AuthService, user_repo: UserRepository
) -> None:
    await user_repo.create(
        email="unverified@example.com", hashed_password=hash_password(_VALID_PASSWORD)
    )

    with pytest.raises(AuthenticationError):
        await service.login(
            LoginRequest(email="unverified@example.com", password=_VALID_PASSWORD),
            _IP,
            _USER_AGENT,
        )


async def test_login_unverified_email_does_not_increment_failed_count(
    service: AuthService, user_repo: UserRepository
) -> None:
    user = await user_repo.create(
        email="unverified2@example.com", hashed_password=hash_password(_VALID_PASSWORD)
    )

    with pytest.raises(AuthenticationError):
        await service.login(
            LoginRequest(email="unverified2@example.com", password="wrong-password"),
            _IP,
            _USER_AGENT,
        )

    fetched = await user_repo.get_by_id(user.id)
    assert fetched is not None
    assert fetched.failed_login_count == 0


# --- OAuth-only account (no password) ------------------------------------------


async def test_login_oauth_only_account_rejected(
    service: AuthService, user_repo: UserRepository
) -> None:
    await user_repo.create(
        email="oauth@example.com",
        hashed_password=None,
        email_verified_at=datetime.now(UTC),
    )

    with pytest.raises(AuthenticationError):
        await service.login(
            LoginRequest(email="oauth@example.com", password=_VALID_PASSWORD), _IP, _USER_AGENT
        )


# --- pepper rotation -----------------------------------------------------------


async def test_login_rehashes_a_password_stored_under_the_previous_pepper(
    service: AuthService,
    user_repo: UserRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rotation has to *finish*, not just not break.

    The fallback in check_password keeps everyone able to log in while
    PASSWORD_PEPPER_OLD is set. This is what lets it be unset again: the one
    moment the plaintext exists is a successful login, so that is when the
    row moves forward. Nothing can sweep the table in the background --
    re-hashing needs the password, which only the user has.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "PASSWORD_PEPPER", "pepper-old")
    monkeypatch.setattr(settings, "PASSWORD_PEPPER_OLD", "")
    user = await user_repo.create(
        email="rotating@example.com",
        hashed_password=hash_password(_VALID_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    stored_before = user.hashed_password

    # The rotation itself.
    monkeypatch.setattr(settings, "PASSWORD_PEPPER", "pepper-new")
    monkeypatch.setattr(settings, "PASSWORD_PEPPER_OLD", "pepper-old")

    await service.login(
        LoginRequest(email="rotating@example.com", password=_VALID_PASSWORD), _IP, _USER_AGENT
    )

    refreshed = await user_repo.get_by_id(user.id)
    assert refreshed is not None
    assert refreshed.hashed_password != stored_before, "the stored hash should be rewritten"

    # The real assertion: it now verifies with the fallback switched off,
    # which is the state the operator is trying to reach.
    monkeypatch.setattr(settings, "PASSWORD_PEPPER_OLD", "")
    assert verify_password(_VALID_PASSWORD, refreshed.hashed_password or "") is True


async def test_login_leaves_an_up_to_date_hash_alone(
    service: AuthService,
    verified_user: User,
    user_repo: UserRepository,
) -> None:
    # The over-correction guard: rewriting on every login would burn an
    # Argon2 hash per request for no reason, and churn the row.
    before = verified_user.hashed_password

    await service.login(
        LoginRequest(email="alice@example.com", password=_VALID_PASSWORD), _IP, _USER_AGENT
    )

    refreshed = await user_repo.get_by_id(verified_user.id)
    assert refreshed is not None
    assert refreshed.hashed_password == before
