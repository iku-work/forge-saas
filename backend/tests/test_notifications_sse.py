"""
Verifies T-103's SSE stream: app.modules.notifications.sse._event_stream
delivers a published message within ~1s and falls back to a ": ping"
keepalive on silence, closing it cleanly unsubscribes from Redis and
releases the connection-slot counters, and the per-user/global connection
caps reject opening a *new* stream once full -- all exercised directly
against the generator/helper functions (an in-process Redis publisher, per
the T-103 spec) rather than through a real HTTP response, since the
installed httpx ASGITransport only returns a Response after the whole ASGI
call completes (see ASGITransport.handle_async_request), which an endlessly
looping StreamingResponse generator never does on its own -- there is no way
to read a partial body from a still-open stream through that transport.
The HTTP-level tests below stick to outcomes that *do* complete normally:
the connection-cap rejection happens before the generator/stream is ever
entered, so a request that hits either cap returns a normal 429 envelope
without hanging.

Same real-Postgres + real-Redis testcontainers / httpx.AsyncClient(
ASGITransport(...)) pattern as test_gdpr_router.py -- only the User table is
needed here since get_current_user is the only DB-touching dependency on the
stream route.
"""

import asyncio
from collections.abc import AsyncGenerator, Callable, Iterator
from uuid import uuid4

import httpx
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

import app.modules.notifications.sse as sse_module
from app.core.db import get_session
from app.core.dependencies import get_redis
from app.core.exceptions import RateLimitError
from app.main import app
from app.middleware import rate_limit as rate_limit_module
from app.modules.auth.models import User
from app.modules.auth.utils import create_access_token
from app.modules.notifications.service import IN_APP_CHANNEL_PREFIX

_TABLES = [User.__table__]


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
def session_factory(test_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as s:
        yield s


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
    # Same swap-the-singleton's-storage approach as test_gdpr_router.py: the
    # api_limit decorator (used by the REST endpoints, not /stream itself,
    # but SlowAPIMiddleware still consults the limiter for every route) closes
    # over the one `limiter` object built at import time, so pointing its
    # storage at a disposable Redis (and restoring it after) is how requests
    # against it actually get counted.
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


@pytest.fixture
async def user(session: AsyncSession) -> User:
    u = User(email=f"user-{uuid4()}@example.com")
    session.add(u)
    await session.flush()
    await session.commit()
    return u


def _auth_header(user: User) -> dict[str, str]:
    token = create_access_token(user.id, None, ())
    return {"Authorization": f"Bearer {token}"}


class _FakeSettings:
    def __init__(self, sse_max_connections: int) -> None:
        self.SSE_MAX_CONNECTIONS = sse_max_connections


async def _wait_for_subscriber(redis_client: Redis, channel: str) -> None:
    for _ in range(50):
        [(_, count)] = await redis_client.pubsub_numsub(channel)
        if count >= 1:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"no subscriber registered on {channel!r} in time")


# --- _event_stream -----------------------------------------------------------------


async def test_event_stream_delivers_published_message_within_one_second(
    redis_client: Redis,
) -> None:
    user_id = uuid4()
    channel = f"{IN_APP_CHANNEL_PREFIX}{user_id}"
    gen = sse_module._event_stream(redis_client, user_id)
    try:
        task = asyncio.create_task(gen.__anext__())
        await _wait_for_subscriber(redis_client, channel)

        await redis_client.publish(channel, '{"type": "member_invited"}')
        chunk = await asyncio.wait_for(task, timeout=1.0)

        assert chunk == 'event: notification\ndata: {"type": "member_invited"}\n\n'
    finally:
        await gen.aclose()


async def test_event_stream_yields_ping_when_no_message_arrives(
    redis_client: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sse_module, "_PING_INTERVAL_SECONDS", 0.1)
    gen = sse_module._event_stream(redis_client, uuid4())
    try:
        first = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        assert first == ": ping\n\n"
        # A second timeout exercises the loop actually looping back around
        # (the `continue` after the first ping), not just yielding once.
        second = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        assert second == ": ping\n\n"
    finally:
        await gen.aclose()


async def test_event_stream_close_unsubscribes_and_releases_connection_slot(
    redis_client: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sse_module, "_PING_INTERVAL_SECONDS", 0.1)
    user_id = uuid4()
    channel = f"{IN_APP_CHANNEL_PREFIX}{user_id}"
    # Simulates the route's _acquire_connection_slot having already run.
    await redis_client.incr(sse_module._user_connections_key(user_id))
    await redis_client.incr(sse_module._TOTAL_CONNECTIONS_KEY)

    gen = sse_module._event_stream(redis_client, user_id)
    await asyncio.wait_for(gen.__anext__(), timeout=1.0)  # let subscribe() actually run
    await gen.aclose()

    [(_, subscriber_count)] = await redis_client.pubsub_numsub(channel)
    assert subscriber_count == 0
    assert await redis_client.get(sse_module._user_connections_key(user_id)) in (None, b"0")
    assert await redis_client.get(sse_module._TOTAL_CONNECTIONS_KEY) in (None, b"0")


# --- connection caps -----------------------------------------------------------------


async def test_acquire_connection_slot_rejects_sixth_for_same_user(redis_client: Redis) -> None:
    user_id = uuid4()
    for _ in range(5):
        await sse_module._acquire_connection_slot(redis_client, user_id)

    with pytest.raises(RateLimitError):
        await sse_module._acquire_connection_slot(redis_client, user_id)

    # Rejection rolls back the increment -- the user's count stays at 5, not 6.
    assert await redis_client.get(sse_module._user_connections_key(user_id)) == b"5"


async def test_acquire_connection_slot_per_user_caps_are_independent(redis_client: Redis) -> None:
    saturated_user, other_user = uuid4(), uuid4()
    for _ in range(5):
        await sse_module._acquire_connection_slot(redis_client, saturated_user)

    # A different user isn't blocked by another user's exhausted cap.
    await sse_module._acquire_connection_slot(redis_client, other_user)


async def test_acquire_connection_slot_rejects_when_total_capacity_exceeded(
    redis_client: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sse_module, "get_settings", lambda: _FakeSettings(sse_max_connections=2))
    await sse_module._acquire_connection_slot(redis_client, uuid4())
    await sse_module._acquire_connection_slot(redis_client, uuid4())

    with pytest.raises(RateLimitError):
        await sse_module._acquire_connection_slot(redis_client, uuid4())


# --- HTTP-level: only outcomes that complete without entering the stream -----------


async def test_stream_requires_auth(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/notifications/stream")

    assert response.status_code == 401


async def test_stream_rejects_when_per_user_limit_already_reached(
    client: httpx.AsyncClient, redis_client: Redis, user: User
) -> None:
    await redis_client.set(sse_module._user_connections_key(user.id), 5)

    response = await client.get("/api/v1/notifications/stream", headers=_auth_header(user))

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"


async def test_stream_rejects_when_total_capacity_reached(
    client: httpx.AsyncClient, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sse_module, "get_settings", lambda: _FakeSettings(sse_max_connections=0))

    response = await client.get("/api/v1/notifications/stream", headers=_auth_header(user))

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
