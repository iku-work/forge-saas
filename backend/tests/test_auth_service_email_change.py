"""Verifies T-226's production-ready change-email flow: request_email_change/
confirm_email_change/cancel_email_change on AuthService. Enumeration-safety
(silently no-ops for an already-registered target address) mirrors
request_password_reset's pattern in test_auth_service_password_reset_magic_link.py;
the token round-trip mirrors that file's password-reset-token tests. Uses the
same real-Postgres testcontainers pattern as the rest of this module's test
suite, plus conftest.py's real-Redis `_redis_client` fixture for the
per-target rate limiter.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel
from testcontainers.postgres import PostgresContainer

from app.core.config import get_settings
from app.core.exceptions import AuthenticationError, ConflictError, RateLimitError
from app.modules.auth.models import EmailChangeToken, User
from app.modules.auth.models import Session as SessionModel
from app.modules.auth.repository import EmailChangeTokenRepository, UserRepository
from app.modules.auth.service import AuthService
from app.modules.auth.utils import hash_password

# T-226: confirm_email_change now revokes every session on the user it
# touches (see AuthService.confirm_email_change), so the session table has to
# exist in this test module's isolated schema too, not just user/
# email_change_token.
_TABLES = [User.__table__, EmailChangeToken.__table__, SessionModel.__table__]
_VALID_PASSWORD = "Correct-Horse9"
_IP = "127.0.0.1"
_USER_AGENT = "pytest"


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


@pytest.fixture
async def oauth_only_user(user_repo: UserRepository) -> User:
    return await user_repo.create(
        email="oauth-alice@example.com",
        hashed_password=None,
        email_verified_at=datetime.now(UTC),
    )


async def _request(
    service: AuthService,
    redis: Redis,
    user: User,
    new_email: str,
    current_password: str | None = _VALID_PASSWORD,
) -> None:
    await service.request_email_change(user, new_email, current_password, _IP, _USER_AGENT, redis)


def _confirm_url_token(enqueued_jobs: list[tuple[str, dict[str, Any]]]) -> str:
    change_email_jobs = [
        kwargs for name, kwargs in enqueued_jobs if kwargs.get("template") == "change_email"
    ]
    assert len(change_email_jobs) == 1
    url: str = change_email_jobs[0]["context"]["confirm_url"]
    return url.rsplit("token=", 1)[1]


def _cancel_url_token(enqueued_jobs: list[tuple[str, dict[str, Any]]]) -> str:
    notifications = [
        kwargs
        for name, kwargs in enqueued_jobs
        if kwargs.get("template") == "security_notification"
        and kwargs["context"]["event"] == "email_change_requested"
    ]
    assert len(notifications) == 1
    url: str = notifications[0]["context"]["cancel_url"]
    return url.rsplit("token=", 1)[1]


# --- request_email_change ---------------------------------------------------------


async def test_request_email_change_same_address_raises_conflict(
    service: AuthService,
    verified_user: User,
    _redis_client: Redis,  # noqa: PT019 -- conftest names it this; its value is used below
) -> None:
    with pytest.raises(ConflictError):
        await _request(service, _redis_client, verified_user, verified_user.email)


async def test_request_email_change_wrong_password_raises_authentication_error(
    service: AuthService,
    verified_user: User,
    _redis_client: Redis,  # noqa: PT019 -- conftest names it this; its value is used below
) -> None:
    with pytest.raises(AuthenticationError):
        await _request(
            service, _redis_client, verified_user, "new@example.com", current_password="wrong"
        )


async def test_request_email_change_missing_password_raises_authentication_error(
    service: AuthService,
    verified_user: User,
    _redis_client: Redis,  # noqa: PT019 -- conftest names it this; its value is used below
) -> None:
    with pytest.raises(AuthenticationError):
        await _request(
            service, _redis_client, verified_user, "new@example.com", current_password=None
        )


async def test_request_email_change_oauth_only_user_skips_password_check(
    service: AuthService,
    oauth_only_user: User,
    _redis_client: Redis,  # noqa: PT019 -- conftest names it this; its value is used below
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    # §T-023: no password to re-prove, so None sails through -- unlike
    # verified_user above, where the same call raises.
    await _request(
        service, _redis_client, oauth_only_user, "new@example.com", current_password=None
    )

    change_email_jobs = [
        kwargs for name, kwargs in enqueued_jobs if kwargs.get("template") == "change_email"
    ]
    assert len(change_email_jobs) == 1


async def test_request_email_change_sends_confirm_link_to_new_address(
    service: AuthService,
    verified_user: User,
    _redis_client: Redis,  # noqa: PT019 -- conftest names it this; its value is used below
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    await _request(service, _redis_client, verified_user, "new@example.com")

    change_email_jobs = [
        kwargs for name, kwargs in enqueued_jobs if kwargs.get("template") == "change_email"
    ]
    assert len(change_email_jobs) == 1
    assert change_email_jobs[0]["to"] == "new@example.com"


async def test_request_email_change_notifies_old_address_with_cancel_link(
    service: AuthService,
    verified_user: User,
    _redis_client: Redis,  # noqa: PT019 -- conftest names it this; its value is used below
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    await _request(service, _redis_client, verified_user, "new@example.com")

    notifications = [
        kwargs
        for name, kwargs in enqueued_jobs
        if kwargs.get("template") == "security_notification"
    ]
    assert len(notifications) == 1
    assert notifications[0]["to"] == verified_user.email
    assert notifications[0]["context"]["event"] == "email_change_requested"
    assert notifications[0]["context"]["new_email"] == "new@example.com"
    assert notifications[0]["context"]["cancel_url"] is not None


async def test_request_email_change_already_registered_email_silently_noops(
    service: AuthService,
    verified_user: User,
    user_repo: UserRepository,
    _redis_client: Redis,  # noqa: PT019 -- conftest names it this; its value is used below
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    other = await user_repo.create(
        email="bob@example.com",
        hashed_password=hash_password(_VALID_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )

    await _request(service, _redis_client, verified_user, other.email)

    assert enqueued_jobs == []


async def test_request_email_change_target_rate_limited(
    service: AuthService,
    verified_user: User,
    _redis_client: Redis,  # noqa: PT019 -- conftest names it this; its value is used below
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    # Same target address, requested repeatedly -- the request itself is
    # otherwise valid each time, so only the per-target counter should stop
    # the 4th from going out.
    for _ in range(3):
        await _request(service, _redis_client, verified_user, "target@example.com")
        enqueued_jobs.clear()

    with pytest.raises(RateLimitError):
        await _request(service, _redis_client, verified_user, "target@example.com")


# --- confirm_email_change ----------------------------------------------------------


async def test_confirm_email_change_happy_path_updates_email(
    service: AuthService,
    verified_user: User,
    user_repo: UserRepository,
    _redis_client: Redis,  # noqa: PT019 -- conftest names it this; its value is used below
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    await _request(service, _redis_client, verified_user, "new@example.com")
    token = _confirm_url_token(enqueued_jobs)

    updated = await service.confirm_email_change(token)

    assert updated.email == "new@example.com"
    refetched = await user_repo.get_by_id(verified_user.id)
    assert refetched is not None
    assert refetched.email == "new@example.com"


async def test_confirm_email_change_marks_new_address_verified(
    service: AuthService,
    verified_user: User,
    _redis_client: Redis,  # noqa: PT019 -- conftest names it this; its value is used below
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    # T-226: the confirm link itself is proof of control over the new
    # address, the same proof verify_email() relies on for a fresh signup.
    await _request(service, _redis_client, verified_user, "new@example.com")
    token = _confirm_url_token(enqueued_jobs)

    updated = await service.confirm_email_change(token)

    assert updated.email_verified_at is not None


async def test_confirm_email_change_notifies_old_address_not_new(
    service: AuthService,
    verified_user: User,
    _redis_client: Redis,  # noqa: PT019 -- conftest names it this; its value is used below
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    old_email = verified_user.email
    await _request(service, _redis_client, verified_user, "new@example.com")
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
    _redis_client: Redis,  # noqa: PT019 -- conftest names it this; its value is used below
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    await _request(service, _redis_client, verified_user, "new@example.com")
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
    _redis_client: Redis,  # noqa: PT019 -- conftest names it this; its value is used below
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    await _request(service, _redis_client, verified_user, "new@example.com")
    token = _confirm_url_token(enqueued_jobs)
    # Someone else registers the target address before the link is clicked.
    await user_repo.create(
        email="new@example.com",
        hashed_password=hash_password(_VALID_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )

    with pytest.raises(ConflictError):
        await service.confirm_email_change(token)


# --- cancel_email_change ------------------------------------------------------------


async def test_cancel_email_change_happy_path_notifies_old_address(
    service: AuthService,
    verified_user: User,
    _redis_client: Redis,  # noqa: PT019 -- conftest names it this; its value is used below
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    await _request(service, _redis_client, verified_user, "new@example.com")
    cancel_token = _cancel_url_token(enqueued_jobs)
    enqueued_jobs.clear()

    await service.cancel_email_change(cancel_token)

    notifications = [
        kwargs
        for name, kwargs in enqueued_jobs
        if kwargs.get("template") == "security_notification"
    ]
    assert len(notifications) == 1
    assert notifications[0]["to"] == verified_user.email
    assert notifications[0]["context"]["event"] == "email_change_cancelled"


async def test_cancel_email_change_invalidates_the_confirm_link_too(
    service: AuthService,
    verified_user: User,
    user_repo: UserRepository,
    _redis_client: Redis,  # noqa: PT019 -- conftest names it this; its value is used below
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    await _request(service, _redis_client, verified_user, "new@example.com")
    confirm_token = _confirm_url_token(enqueued_jobs)
    cancel_token = _cancel_url_token(enqueued_jobs)

    await service.cancel_email_change(cancel_token)

    with pytest.raises(AuthenticationError):
        await service.confirm_email_change(confirm_token)
    refetched = await user_repo.get_by_id(verified_user.id)
    assert refetched is not None
    assert refetched.email == verified_user.email


async def test_cancel_email_change_token_is_single_use(
    service: AuthService,
    verified_user: User,
    _redis_client: Redis,  # noqa: PT019 -- conftest names it this; its value is used below
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    await _request(service, _redis_client, verified_user, "new@example.com")
    cancel_token = _cancel_url_token(enqueued_jobs)
    await service.cancel_email_change(cancel_token)

    with pytest.raises(AuthenticationError):
        await service.cancel_email_change(cancel_token)


async def test_cancel_email_change_garbage_token_raises(service: AuthService) -> None:
    with pytest.raises(AuthenticationError):
        await service.cancel_email_change("not-a-real-token")


async def test_cancel_email_change_confirm_token_does_not_work_as_cancel_token(
    service: AuthService,
    verified_user: User,
    _redis_client: Redis,  # noqa: PT019 -- conftest names it this; its value is used below
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    await _request(service, _redis_client, verified_user, "new@example.com")
    confirm_token = _confirm_url_token(enqueued_jobs)

    with pytest.raises(AuthenticationError):
        await service.cancel_email_change(confirm_token)


# --- locale-prefixed deep links (T-233) ---------------------------------------


async def test_change_email_links_carry_the_recipients_locale(
    service: AuthService,
    verified_user: User,
    session: AsyncSession,
    _redis_client: Redis,  # noqa: PT019 -- conftest names it this; its value is used below
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    """Both halves of the flow -- the confirm link mailed to the *new*
    address and the cancel link mailed to the current one -- are built from
    the account owner's own locale, not from a request-scoped default."""
    verified_user.locale = "fr"
    session.add(verified_user)
    await session.flush()

    await _request(service, _redis_client, verified_user, "new@example.com")

    confirm_job = next(k for _, k in enqueued_jobs if k.get("template") == "change_email")
    assert "/fr/confirm-email-change?token=" in confirm_job["context"]["confirm_url"]

    notification = next(
        k
        for _, k in enqueued_jobs
        if k.get("template") == "security_notification"
        and k["context"]["event"] == "email_change_requested"
    )
    assert "/fr/cancel-email-change?token=" in notification["context"]["cancel_url"]
    assert notification["context"]["settings_url"] == (
        f"{get_settings().FRONTEND_URL}/fr/settings/security"
    )
