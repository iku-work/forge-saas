"""
Verifies T-216 (authenticated change-password): the current password is
re-verified before a change is accepted, the new password takes effect,
every other session is revoked while the session the request itself is
authenticated with survives, and a security-notification email is enqueued.
Uses the same real-Postgres testcontainers pattern as
test_auth_service_password_reset_magic_link.py.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel
from testcontainers.postgres import PostgresContainer

from app.core.exceptions import AuthenticationError
from app.modules.auth.models import Session as SessionModel
from app.modules.auth.models import User
from app.modules.auth.repository import SessionRepository, UserRepository
from app.modules.auth.service import AuthService
from app.modules.auth.utils import generate_refresh_token, hash_password, verify_password

_TABLES = [User.__table__, SessionModel.__table__]
_VALID_PASSWORD = "Correct-Horse9"
_NEW_PASSWORD = "Even-Better-Horse8"
_IP = "127.0.0.1"
_USER_AGENT = "pytest"


@pytest.fixture(scope="module")
def postgres_container() -> "PostgresContainer":
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


# Function-scoped (not module-scoped): see test_auth_repository.py -- an
# asyncpg-backed engine can't be reused across pytest-asyncio's per-test loop.
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


async def _issue_session(session_repo: SessionRepository, user: User) -> str:
    plain, token_hash = generate_refresh_token()
    await session_repo.create(
        user_id=user.id,
        token_hash=token_hash,
        device_label=None,
        ip_address=_IP,
        user_agent=_USER_AGENT,
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    return plain


async def test_change_password_updates_password(
    service: AuthService, verified_user: User, user_repo: UserRepository
) -> None:
    await service.change_password(verified_user, _VALID_PASSWORD, _NEW_PASSWORD, None)

    updated = await user_repo.get_by_id(verified_user.id)
    assert updated is not None
    assert updated.hashed_password is not None
    assert verify_password(_NEW_PASSWORD, updated.hashed_password)
    assert not verify_password(_VALID_PASSWORD, updated.hashed_password)


async def test_change_password_wrong_current_password_raises(
    service: AuthService, verified_user: User
) -> None:
    with pytest.raises(AuthenticationError):
        await service.change_password(verified_user, "wrong-password", _NEW_PASSWORD, None)


async def test_change_password_wrong_current_password_leaves_password_unchanged(
    service: AuthService, verified_user: User, user_repo: UserRepository
) -> None:
    with pytest.raises(AuthenticationError):
        await service.change_password(verified_user, "wrong-password", _NEW_PASSWORD, None)

    unchanged = await user_repo.get_by_id(verified_user.id)
    assert unchanged is not None
    assert verify_password(_VALID_PASSWORD, unchanged.hashed_password or "")


async def test_change_password_revokes_other_sessions(
    service: AuthService, verified_user: User, session_repo: SessionRepository
) -> None:
    await _issue_session(session_repo, verified_user)
    await _issue_session(session_repo, verified_user)

    await service.change_password(verified_user, _VALID_PASSWORD, _NEW_PASSWORD, None)

    active = await session_repo.list_active_for_user(verified_user.id)
    assert active == []


async def test_change_password_keeps_current_session_alive(
    service: AuthService, verified_user: User, session_repo: SessionRepository
) -> None:
    current_session_plain = await _issue_session(session_repo, verified_user)
    await _issue_session(session_repo, verified_user)  # a second, unrelated session

    await service.change_password(
        verified_user, _VALID_PASSWORD, _NEW_PASSWORD, current_session_plain
    )

    active = await session_repo.list_active_for_user(verified_user.id)
    assert len(active) == 1


async def test_change_password_enqueues_security_notification_email(
    service: AuthService,
    verified_user: User,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    await service.change_password(verified_user, _VALID_PASSWORD, _NEW_PASSWORD, None)

    email_jobs = [kwargs for name, kwargs in enqueued_jobs if name == "send_email"]
    assert len(email_jobs) == 1
    assert email_jobs[0]["template"] == "security_notification"
    assert email_jobs[0]["context"]["event"] == "password_change"
    assert email_jobs[0]["to"] == verified_user.email
