"""
Verifies T-218's minimal change-email flow: request_email_change/
confirm_email_change on AuthService. Enumeration-safety (silently no-ops for
an already-registered target address) mirrors request_password_reset's
pattern in test_auth_service_password_reset_magic_link.py; the token
round-trip mirrors that file's password-reset-token tests. Uses the same
real-Postgres testcontainers pattern as the rest of this module's test suite.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
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

from app.core.exceptions import AuthenticationError, ConflictError
from app.modules.auth.models import EmailChangeToken, User
from app.modules.auth.repository import EmailChangeTokenRepository, UserRepository
from app.modules.auth.service import AuthService
from app.modules.auth.utils import hash_password

_TABLES = [User.__table__, EmailChangeToken.__table__]
_VALID_PASSWORD = "Correct-Horse9"


@pytest.fixture(scope="module")
def postgres_container() -> "PostgresContainer":
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


# Function-scoped: see test_auth_repository.py — an asyncpg-backed engine
# can't be reused across pytest-asyncio's per-test event loop.
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
def email_change_repo(session: AsyncSession) -> EmailChangeTokenRepository:
    return EmailChangeTokenRepository(session)


@pytest.fixture
async def verified_user(user_repo: UserRepository) -> User:
    return await user_repo.create(
        email="alice@example.com",
        hashed_password=hash_password(_VALID_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )


def _confirm_url_token(enqueued_jobs: list[tuple[str, dict[str, Any]]]) -> str:
    change_email_jobs = [
        kwargs for name, kwargs in enqueued_jobs if kwargs.get("template") == "change_email"
    ]
    assert len(change_email_jobs) == 1
    url: str = change_email_jobs[0]["context"]["confirm_url"]
    return url.rsplit("token=", 1)[1]


# --- request_email_change ---------------------------------------------------------


async def test_request_email_change_same_address_raises_conflict(
    service: AuthService, verified_user: User
) -> None:
    with pytest.raises(ConflictError):
        await service.request_email_change(verified_user, verified_user.email)


async def test_request_email_change_sends_confirm_link_to_new_address(
    service: AuthService,
    verified_user: User,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    await service.request_email_change(verified_user, "new@example.com")

    change_email_jobs = [
        kwargs for name, kwargs in enqueued_jobs if kwargs.get("template") == "change_email"
    ]
    assert len(change_email_jobs) == 1
    assert change_email_jobs[0]["to"] == "new@example.com"


async def test_request_email_change_notifies_old_address(
    service: AuthService,
    verified_user: User,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    await service.request_email_change(verified_user, "new@example.com")

    notifications = [
        kwargs
        for name, kwargs in enqueued_jobs
        if kwargs.get("template") == "security_notification"
    ]
    assert len(notifications) == 1
    assert notifications[0]["to"] == verified_user.email
    assert notifications[0]["context"]["event"] == "email_change_requested"
    assert notifications[0]["context"]["new_email"] == "new@example.com"


async def test_request_email_change_already_registered_email_silently_noops(
    service: AuthService,
    verified_user: User,
    user_repo: UserRepository,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    other = await user_repo.create(
        email="bob@example.com",
        hashed_password=hash_password(_VALID_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )

    await service.request_email_change(verified_user, other.email)

    assert enqueued_jobs == []


# --- confirm_email_change ----------------------------------------------------------


async def test_confirm_email_change_happy_path_updates_email(
    service: AuthService,
    verified_user: User,
    user_repo: UserRepository,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    await service.request_email_change(verified_user, "new@example.com")
    token = _confirm_url_token(enqueued_jobs)

    updated = await service.confirm_email_change(token)

    assert updated.email == "new@example.com"
    refetched = await user_repo.get_by_id(verified_user.id)
    assert refetched is not None
    assert refetched.email == "new@example.com"


async def test_confirm_email_change_notifies_old_address_not_new(
    service: AuthService,
    verified_user: User,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    old_email = verified_user.email
    await service.request_email_change(verified_user, "new@example.com")
    token = _confirm_url_token(enqueued_jobs)
    enqueued_jobs.clear()

    await service.confirm_email_change(token)

    notifications = [
        kwargs
        for name, kwargs in enqueued_jobs
        if kwargs.get("template") == "security_notification"
    ]
    assert len(notifications) == 1
    assert notifications[0]["to"] == old_email
    assert notifications[0]["context"]["event"] == "email_changed"
    assert notifications[0]["context"]["new_email"] == "new@example.com"


async def test_confirm_email_change_token_is_single_use(
    service: AuthService,
    verified_user: User,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    await service.request_email_change(verified_user, "new@example.com")
    token = _confirm_url_token(enqueued_jobs)
    await service.confirm_email_change(token)

    with pytest.raises(AuthenticationError):
        await service.confirm_email_change(token)


async def test_confirm_email_change_garbage_token_raises(service: AuthService) -> None:
    with pytest.raises(AuthenticationError):
        await service.confirm_email_change("not-a-real-token")


async def test_confirm_email_change_target_claimed_in_the_interim_raises_conflict(
    service: AuthService,
    verified_user: User,
    user_repo: UserRepository,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    await service.request_email_change(verified_user, "new@example.com")
    token = _confirm_url_token(enqueued_jobs)
    # Someone else registers the target address before the link is clicked.
    await user_repo.create(
        email="new@example.com",
        hashed_password=hash_password(_VALID_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )

    with pytest.raises(ConflictError):
        await service.confirm_email_change(token)
