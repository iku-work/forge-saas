"""
Verifies T-102's NotificationService.send(): channel fan-out is driven by
the recipient's resolved preferences (T-101.5's channels_for) -- email and
push go through app.core.jobs.enqueue_job (mocked here via conftest.py's
autouse `enqueued_jobs` fixture, never a real Redis-backed ARQ pool),
in-app publishes to the `notif:user:{id}` Redis channel, the idempotency_key
short-circuits a duplicate send within 24h, and every send writes an
AuditEvent. Same real-Postgres + real-Redis testcontainers pattern as
test_tenancy_service.py / test_dependencies.py.
"""

from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from app.core.audit_context import reset_impersonator_id, set_impersonator_id
from app.core.exceptions import NotFoundError
from app.modules.audit.models import AuditEvent
from app.modules.auth.models import User
from app.modules.billing.models import Plan
from app.modules.notifications.models import Notification
from app.modules.notifications.service import NotificationService
from app.modules.tenancy.models import Organization

_TABLES = [
    User.__table__,
    Plan.__table__,
    Organization.__table__,
    Notification.__table__,
    AuditEvent.__table__,
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
def service(session: AsyncSession, redis_client: Redis) -> NotificationService:
    return NotificationService(session, redis_client)


@pytest.fixture
async def org(session: AsyncSession) -> Organization:
    organization = Organization(name="Acme", slug=f"acme-{uuid4()}")
    session.add(organization)
    await session.flush()
    return organization


@pytest.fixture
async def user(session: AsyncSession) -> User:
    u = User(email=f"user-{uuid4()}@example.com")
    session.add(u)
    await session.flush()
    return u


# --- channel fan-out -----------------------------------------------------------


async def test_send_persists_notification_with_default_channels(
    service: NotificationService, user: User, org: Organization
) -> None:
    notification = await service.send(
        user_id=user.id,
        org_id=org.id,
        type="member_invited",
        params={"email": "new@example.com", "org_name": "Acme"},
    )

    assert notification.user_id == user.id
    assert notification.org_id == org.id
    assert set(notification.channels) == {"email", "in_app"}  # member_invited default, push=False


async def test_send_email_only_type_enqueues_only_email_job(
    service: NotificationService,
    user: User,
    org: Organization,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    # weekly_summary's DEFAULT_PREFERENCES is {email: True, in_app: False, push: False}.
    await service.send(
        user_id=user.id, org_id=org.id, type="weekly_summary", params={"org_name": "Acme"}
    )

    functions = [function for function, _ in enqueued_jobs]
    assert functions == ["send_email"]


async def test_send_payment_failed_enqueues_both_email_and_push(
    service: NotificationService,
    user: User,
    org: Organization,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    # payment_failed's DEFAULT_PREFERENCES enables all three channels.
    notification = await service.send(
        user_id=user.id,
        org_id=org.id,
        type="payment_failed",
        params={"org_name": "Acme", "amount": "$49.00"},
    )

    functions = {function for function, _ in enqueued_jobs}
    assert functions == {"send_email", "send_push"}
    push_kwargs = next(kwargs for function, kwargs in enqueued_jobs if function == "send_push")
    assert push_kwargs == {"user_id": user.id, "notification_id": notification.id}


async def test_send_drops_only_the_push_channel_while_impersonating(
    service: NotificationService,
    user: User,
    org: Organization,
    session: AsyncSession,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    """T-260: the ContextVar app.middleware.impersonation sets for the whole
    request. Email still goes (it is a record the user can read at their own
    pace); the push, which lights up a device in their hand because a support
    agent clicked something, does not."""
    token = set_impersonator_id(uuid4())
    try:
        notification = await service.send(
            user_id=user.id,
            org_id=org.id,
            type="payment_failed",
            params={"org_name": "Acme", "amount": "$49.00"},
        )
    finally:
        reset_impersonator_id(token)

    assert [function for function, _ in enqueued_jobs] == ["send_email"]
    # The row itself is untouched: the user's own history stays complete, and
    # the in-app feed still shows what happened.
    assert "push" in notification.channels
    event = (
        (
            await session.execute(
                select(AuditEvent).where(AuditEvent.resource_id == str(notification.id))
            )
        )
        .scalars()
        .one()
    )
    assert event.diff is not None
    assert event.diff["push_suppressed"] is True


async def test_send_outside_impersonation_records_no_suppression(
    service: NotificationService,
    user: User,
    org: Organization,
    session: AsyncSession,
) -> None:
    notification = await service.send(
        user_id=user.id,
        org_id=org.id,
        type="payment_failed",
        params={"org_name": "Acme", "amount": "$49.00"},
    )

    event = (
        (
            await session.execute(
                select(AuditEvent).where(AuditEvent.resource_id == str(notification.id))
            )
        )
        .scalars()
        .one()
    )
    assert event.diff is not None
    assert "push_suppressed" not in event.diff


async def test_send_in_app_channel_publishes_to_redis(
    service: NotificationService, user: User, org: Organization, redis_client: Redis
) -> None:
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(f"notif:user:{user.id}")
    try:
        await pubsub.get_message(timeout=1)  # subscribe confirmation

        await service.send(
            user_id=user.id,
            org_id=org.id,
            type="member_invited",
            params={"email": "new@example.com", "org_name": "Acme"},
        )

        message = await pubsub.get_message(timeout=2)
        assert message is not None
        assert message["type"] == "message"
        assert b'"member_invited"' in message["data"]
    finally:
        await pubsub.unsubscribe(f"notif:user:{user.id}")
        await pubsub.aclose()


async def test_send_raises_not_found_for_unknown_user(
    service: NotificationService, org: Organization
) -> None:
    with pytest.raises(NotFoundError):
        await service.send(
            user_id=uuid4(),
            org_id=org.id,
            type="member_invited",
            params={"email": "new@example.com", "org_name": "Acme"},
        )


# --- idempotency -----------------------------------------------------------------


async def test_send_with_same_idempotency_key_is_a_noop_second_time(
    service: NotificationService,
    user: User,
    org: Organization,
    session: AsyncSession,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    first = await service.send(
        user_id=user.id,
        org_id=org.id,
        type="payment_failed",
        params={"org_name": "Acme", "amount": "$49.00"},
        idempotency_key="evt_dedup_1",
    )
    second = await service.send(
        user_id=user.id,
        org_id=org.id,
        type="payment_failed",
        params={"org_name": "Acme", "amount": "$49.00"},
        idempotency_key="evt_dedup_1",
    )

    assert first.id == second.id
    rows = (
        (
            await session.execute(
                select(Notification).where(Notification.idempotency_key == "evt_dedup_1")
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    # Only the first send's channel dispatch should have happened.
    assert len([f for f, _ in enqueued_jobs if f == "send_email"]) == 1


# --- audit logging -----------------------------------------------------------------


async def test_send_writes_audit_event(
    service: NotificationService, user: User, org: Organization, session: AsyncSession
) -> None:
    notification = await service.send(
        user_id=user.id,
        org_id=org.id,
        type="member_invited",
        params={"email": "new@example.com", "org_name": "Acme"},
    )

    event = (
        await session.execute(select(AuditEvent).where(AuditEvent.action == "notification.sent"))
    ).scalar_one()
    assert event.resource_id == str(notification.id)
    assert event.org_id == org.id
    assert event.diff == {
        "type": "member_invited",
        "channels": notification.channels,
        "locale": "en",
    }
