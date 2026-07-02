"""
Verifies T-019 (auth service — token refresh + theft detection): happy-path
rotation revokes the old session and issues a new one, while replaying a
revoked or expired refresh token revokes every session the user has. Uses
the same real-Postgres testcontainers pattern as test_auth_service_login.py.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

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
from app.modules.auth.utils import generate_refresh_token, hash_password, hash_token

_TABLES = [User.__table__, SessionModel.__table__]
_VALID_PASSWORD = "Correct-Horse9"
_IP = "127.0.0.1"
_USER_AGENT = "pytest"


@pytest.fixture(scope="module")
def postgres_container() -> "PostgresContainer":
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


# Function-scoped (not module-scoped): see test_auth_repository.py — an
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


async def _issue_session(
    session_repo: SessionRepository,
    user: User,
    *,
    expires_at: datetime | None = None,
) -> str:
    """Creates a Session row directly and hands back the plain refresh token."""
    plain, token_hash = generate_refresh_token()
    await session_repo.create(
        user_id=user.id,
        token_hash=token_hash,
        device_label=None,
        ip_address=_IP,
        user_agent=_USER_AGENT,
        expires_at=expires_at or datetime.now(UTC) + timedelta(days=30),
    )
    return plain


# --- happy path ----------------------------------------------------------------


async def test_refresh_happy_path_returns_new_tokens(
    service: AuthService, verified_user: User, session_repo: SessionRepository
) -> None:
    plain = await _issue_session(session_repo, verified_user)

    access_token, new_refresh_plain = await service.refresh(plain, _IP, _USER_AGENT)

    assert access_token
    assert new_refresh_plain
    assert new_refresh_plain != plain


async def test_refresh_happy_path_revokes_old_session(
    service: AuthService, verified_user: User, session_repo: SessionRepository
) -> None:
    plain = await _issue_session(session_repo, verified_user)

    await service.refresh(plain, _IP, _USER_AGENT)

    old_session = await session_repo.get_by_token_hash(hash_token(plain))
    assert old_session is not None
    assert old_session.revoked_at is not None


async def test_refresh_happy_path_creates_exactly_one_new_active_session(
    service: AuthService, verified_user: User, session_repo: SessionRepository
) -> None:
    plain = await _issue_session(session_repo, verified_user)

    await service.refresh(plain, _IP, _USER_AGENT)

    active = await session_repo.list_active_for_user(verified_user.id)
    assert len(active) == 1


async def test_refresh_new_token_can_be_used_again(
    service: AuthService, verified_user: User, session_repo: SessionRepository
) -> None:
    plain = await _issue_session(session_repo, verified_user)

    _, second_plain = await service.refresh(plain, _IP, _USER_AGENT)
    access_token, third_plain = await service.refresh(second_plain, _IP, _USER_AGENT)

    assert access_token
    assert third_plain


# --- theft detection ---------------------------------------------------------------


async def test_refresh_unknown_token_raises(service: AuthService) -> None:
    with pytest.raises(AuthenticationError):
        await service.refresh("not-a-real-token", _IP, _USER_AGENT)


async def test_refresh_reuse_of_revoked_token_raises(
    service: AuthService, verified_user: User, session_repo: SessionRepository
) -> None:
    plain = await _issue_session(session_repo, verified_user)
    await service.refresh(plain, _IP, _USER_AGENT)  # rotates -- revokes `plain`'s session

    with pytest.raises(AuthenticationError):
        await service.refresh(plain, _IP, _USER_AGENT)


async def test_refresh_reuse_of_revoked_token_revokes_all_user_sessions(
    service: AuthService, verified_user: User, session_repo: SessionRepository
) -> None:
    plain = await _issue_session(session_repo, verified_user)
    _, rotated_plain = await service.refresh(plain, _IP, _USER_AGENT)

    with pytest.raises(AuthenticationError):
        await service.refresh(plain, _IP, _USER_AGENT)  # replay of the now-revoked token

    active = await session_repo.list_active_for_user(verified_user.id)
    assert active == []
    # Even the session created by the legitimate rotation above is gone.
    rotated_session = await session_repo.get_by_token_hash(hash_token(rotated_plain))
    assert rotated_session is not None
    assert rotated_session.revoked_at is not None


async def test_refresh_expired_token_raises(
    service: AuthService, verified_user: User, session_repo: SessionRepository
) -> None:
    plain = await _issue_session(
        session_repo, verified_user, expires_at=datetime.now(UTC) - timedelta(minutes=1)
    )

    with pytest.raises(AuthenticationError):
        await service.refresh(plain, _IP, _USER_AGENT)
