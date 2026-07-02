"""
Verifies T-102's NotificationRepository and PushTokenRepository: cross-tenant
and cross-user isolation on mark_read (a notification scoped to another org,
or to another user in the *same* org, is treated as missing -- mirrors
test_tenancy_service.py's "indistinguishable from missing" cross-org checks),
cursor pagination via list_for_user, the unread-count Redis cache (including
that a cache hit skips the DB entirely), the idempotency-key lookup window,
and PushToken's upsert-by-token + soft-delete-by-deactivate behaviour.

Same real-Postgres + real-Redis testcontainers pattern as test_dependencies.py
since unread_count needs a real cache, not a session-scoped fixture
NotificationService's own tests can share.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from app.core.exceptions import NotFoundError
from app.modules.auth.models import User
from app.modules.billing.models import Plan
from app.modules.notifications.models import Notification, PushToken
from app.modules.notifications.repository import NotificationRepository, PushTokenRepository
from app.modules.tenancy.models import Organization

_TABLES = [
    User.__table__,
    Plan.__table__,
    Organization.__table__,
    Notification.__table__,
    PushToken.__table__,
]


@pytest.fixture(scope="module")
def postgres_container() -> "PostgresContainer":
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture(scope="module")
def redis_container() -> "RedisContainer":
    with RedisContainer("redis:7-alpine") as container:
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
async def redis_client(redis_container: "RedisContainer") -> AsyncGenerator[Redis, None]:
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    client: Redis = Redis.from_url(f"redis://{host}:{port}/0")
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture
async def org(session: AsyncSession) -> Organization:
    organization = Organization(name="Acme", slug=f"acme-{uuid4()}")
    session.add(organization)
    await session.flush()
    return organization


@pytest.fixture
async def other_org(session: AsyncSession) -> Organization:
    organization = Organization(name="Globex", slug=f"globex-{uuid4()}")
    session.add(organization)
    await session.flush()
    return organization


@pytest.fixture
async def user(session: AsyncSession) -> User:
    u = User(email=f"user-{uuid4()}@example.com")
    session.add(u)
    await session.flush()
    return u


@pytest.fixture
async def other_user(session: AsyncSession) -> User:
    u = User(email=f"other-{uuid4()}@example.com")
    session.add(u)
    await session.flush()
    return u


@pytest.fixture
def repo(session: AsyncSession, org: Organization, redis_client: Redis) -> NotificationRepository:
    return NotificationRepository(session, org.id, redis_client)


async def _send(
    repo: NotificationRepository, user_id: object, *, type_: str = "member_invited"
) -> Notification:
    return await repo.create(
        user_id=user_id,
        type=type_,
        title="Title",
        body="Body",
        channels=["in_app"],
    )


# --- create / get_or_raise ---------------------------------------------------


async def test_create_auto_injects_org_id(repo: NotificationRepository, user: User) -> None:
    notification = await _send(repo, user.id)

    assert notification.org_id == repo.org_id


async def test_create_rejects_mismatched_org_id_override(
    repo: NotificationRepository, user: User
) -> None:
    with pytest.raises(ValueError, match="org_id"):
        await repo.create(
            user_id=user.id,
            org_id=uuid4(),
            type="member_invited",
            title="Title",
            body="Body",
            channels=["in_app"],
        )


async def test_get_or_raise_cross_org_raises_not_found(
    session: AsyncSession,
    org: Organization,
    other_org: Organization,
    redis_client: Redis,
    user: User,
) -> None:
    repo_a = NotificationRepository(session, org.id, redis_client)
    repo_b = NotificationRepository(session, other_org.id, redis_client)
    notification = await _send(repo_a, user.id)

    with pytest.raises(NotFoundError):
        await repo_b.get_or_raise(notification.id)


# --- mark_read / mark_all_read ----------------------------------------------


async def test_mark_read_sets_read_at(repo: NotificationRepository, user: User) -> None:
    notification = await _send(repo, user.id)

    updated = await repo.mark_read(notification.id, user.id)

    assert updated.read_at is not None


async def test_mark_read_cross_org_raises_not_found(
    session: AsyncSession,
    org: Organization,
    other_org: Organization,
    redis_client: Redis,
    user: User,
) -> None:
    repo_a = NotificationRepository(session, org.id, redis_client)
    repo_b = NotificationRepository(session, other_org.id, redis_client)
    notification = await _send(repo_a, user.id)

    with pytest.raises(NotFoundError):
        await repo_b.mark_read(notification.id, user.id)


async def test_mark_read_cross_user_same_org_raises_not_found(
    repo: NotificationRepository, user: User, other_user: User
) -> None:
    notification = await _send(repo, user.id)

    with pytest.raises(NotFoundError):
        await repo.mark_read(notification.id, other_user.id)


async def test_mark_all_read_only_affects_unread_for_that_user(
    repo: NotificationRepository, user: User, other_user: User
) -> None:
    first = await _send(repo, user.id)
    second = await _send(repo, user.id)
    await repo.mark_read(first.id, user.id)
    await _send(repo, other_user.id)

    updated_count = await repo.mark_all_read(user.id)

    assert updated_count == 1
    refreshed = await repo.get_or_raise(second.id)
    assert refreshed.read_at is not None


# --- list_for_user ------------------------------------------------------------


async def test_list_for_user_orders_newest_first_and_paginates(
    repo: NotificationRepository, user: User
) -> None:
    for _ in range(3):
        await _send(repo, user.id)

    page_one, cursor = await repo.list_for_user(user.id, limit=2)
    assert len(page_one) == 2
    assert cursor == page_one[-1].id

    page_two, cursor_two = await repo.list_for_user(user.id, after=cursor, limit=2)
    assert len(page_two) == 1
    assert cursor_two is None
    assert {n.id for n in page_one} & {n.id for n in page_two} == set()


async def test_list_for_user_unread_only_filters_read(
    repo: NotificationRepository, user: User
) -> None:
    read_notification = await _send(repo, user.id)
    unread_notification = await _send(repo, user.id)
    await repo.mark_read(read_notification.id, user.id)

    items, _ = await repo.list_for_user(user.id, unread_only=True)

    assert [n.id for n in items] == [unread_notification.id]


async def test_list_for_user_cursor_from_another_user_is_treated_as_no_cursor(
    repo: NotificationRepository, user: User, other_user: User
) -> None:
    other_notification = await _send(repo, other_user.id)
    mine = await _send(repo, user.id)

    items, _ = await repo.list_for_user(user.id, after=other_notification.id)

    assert [n.id for n in items] == [mine.id]


# --- find_recent_by_idempotency_key -------------------------------------------


async def test_find_recent_by_idempotency_key_matches_within_window(
    repo: NotificationRepository, user: User
) -> None:
    notification = await repo.create(
        user_id=user.id,
        type="payment_failed",
        title="Title",
        body="Body",
        channels=["in_app"],
        idempotency_key="evt_123",
    )

    found = await repo.find_recent_by_idempotency_key(user.id, "evt_123")

    assert found is not None
    assert found.id == notification.id


async def test_find_recent_by_idempotency_key_ignores_stale_rows(
    repo: NotificationRepository, user: User, session: AsyncSession
) -> None:
    notification = await repo.create(
        user_id=user.id,
        type="payment_failed",
        title="Title",
        body="Body",
        channels=["in_app"],
        idempotency_key="evt_456",
    )
    notification.created_at = datetime.now(UTC) - timedelta(hours=25)
    await session.flush()

    found = await repo.find_recent_by_idempotency_key(user.id, "evt_456")

    assert found is None


async def test_find_recent_by_idempotency_key_scoped_to_user(
    repo: NotificationRepository, user: User, other_user: User
) -> None:
    await repo.create(
        user_id=other_user.id,
        type="payment_failed",
        title="Title",
        body="Body",
        channels=["in_app"],
        idempotency_key="evt_789",
    )

    found = await repo.find_recent_by_idempotency_key(user.id, "evt_789")

    assert found is None


# --- unread_count -------------------------------------------------------------


async def test_unread_count_counts_only_unread(repo: NotificationRepository, user: User) -> None:
    unread = await _send(repo, user.id)
    read = await _send(repo, user.id)
    await repo.mark_read(read.id, user.id)

    count = await repo.unread_count(user.id)

    assert count == 1
    assert unread.read_at is None


async def test_unread_count_cache_hit_skips_the_database(
    repo: NotificationRepository, user: User, test_engine: AsyncEngine
) -> None:
    await _send(repo, user.id)
    await repo.unread_count(user.id)  # populates the cache

    query_count = 0

    def _count_queries(*_args: object, **_kwargs: object) -> None:
        nonlocal query_count
        query_count += 1

    event.listen(test_engine.sync_engine, "before_cursor_execute", _count_queries)
    try:
        cached_count = await repo.unread_count(user.id)
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", _count_queries)

    assert cached_count == 1
    assert query_count == 0


async def test_unread_count_invalidated_by_mark_read(
    repo: NotificationRepository, user: User
) -> None:
    notification = await _send(repo, user.id)
    await repo.unread_count(user.id)

    await repo.mark_read(notification.id, user.id)

    assert await repo.unread_count(user.id) == 0


# --- PushTokenRepository -------------------------------------------------------


async def test_register_creates_new_token(session: AsyncSession, user: User) -> None:
    repo_tokens = PushTokenRepository(session)

    token = await repo_tokens.register(user.id, "ExponentPushToken[a]", "ios", "iPhone")

    assert token.user_id == user.id
    assert token.platform == "ios"


async def test_register_upserts_by_token_and_reactivates(
    session: AsyncSession, user: User, other_user: User
) -> None:
    repo_tokens = PushTokenRepository(session)
    token = await repo_tokens.register(user.id, "ExponentPushToken[b]", "ios")
    await repo_tokens.deactivate(token.token)

    reregistered = await repo_tokens.register(
        other_user.id, "ExponentPushToken[b]", "android", "Pixel"
    )

    assert reregistered.id == token.id
    assert reregistered.user_id == other_user.id
    assert reregistered.platform == "android"
    assert reregistered.deleted_at is None


async def test_list_active_for_user_excludes_deactivated(session: AsyncSession, user: User) -> None:
    repo_tokens = PushTokenRepository(session)
    active = await repo_tokens.register(user.id, "ExponentPushToken[c]", "ios")
    inactive = await repo_tokens.register(user.id, "ExponentPushToken[d]", "ios")
    await repo_tokens.deactivate(inactive.token)

    tokens = await repo_tokens.list_active_for_user(user.id)

    assert [t.id for t in tokens] == [active.id]


async def test_deactivate_unknown_token_is_a_noop(session: AsyncSession) -> None:
    repo_tokens = PushTokenRepository(session)

    await repo_tokens.deactivate("ExponentPushToken[does-not-exist]")
