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
import time
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
from tests._database import DatabaseHandle

# T-291: this file needs notifications enabled -- its routes, its tables, or
# both. CI's module-matrix job deselects it in the combination that turns
# every optional module off.
pytestmark = pytest.mark.module_dependent

_TABLES = [User.__table__]


@pytest.fixture(scope="module")
def redis_container() -> "RedisContainer":
    with RedisContainer("redis:7-alpine") as container:
        yield container


# Function-scoped (not module-scoped): see test_auth_repository.py -- an
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
    gen = sse_module._event_stream(redis_client, user_id, "conn-under-test")
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
    gen = sse_module._event_stream(redis_client, uuid4(), "conn-under-test")
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
    connection_id = "conn-under-test"
    await sse_module._acquire_connection_slot(redis_client, user_id, connection_id)

    gen = sse_module._event_stream(redis_client, user_id, connection_id)
    await asyncio.wait_for(gen.__anext__(), timeout=1.0)  # let subscribe() actually run
    await gen.aclose()

    [(_, subscriber_count)] = await redis_client.pubsub_numsub(channel)
    assert subscriber_count == 0
    assert await redis_client.zcard(sse_module._user_connections_key(user_id)) == 0
    assert await redis_client.zcard(sse_module._TOTAL_CONNECTIONS_KEY) == 0


# --- connection caps -----------------------------------------------------------------


async def test_acquire_connection_slot_rejects_sixth_for_same_user(redis_client: Redis) -> None:
    user_id = uuid4()
    for i in range(5):
        await sse_module._acquire_connection_slot(redis_client, user_id, f"conn-{i}")

    with pytest.raises(RateLimitError):
        await sse_module._acquire_connection_slot(redis_client, user_id, "conn-6")

    # A rejection adds nothing -- the held set stays at five, not six.
    assert await redis_client.zcard(sse_module._user_connections_key(user_id)) == 5


async def test_acquire_connection_slot_per_user_caps_are_independent(redis_client: Redis) -> None:
    saturated_user, other_user = uuid4(), uuid4()
    for i in range(5):
        await sse_module._acquire_connection_slot(redis_client, saturated_user, f"conn-{i}")

    # A different user isn't blocked by another user's exhausted cap.
    await sse_module._acquire_connection_slot(redis_client, other_user, "conn-0")


async def test_acquire_connection_slot_rejects_when_total_capacity_exceeded(
    redis_client: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sse_module, "get_settings", lambda: _FakeSettings(sse_max_connections=2))
    await sse_module._acquire_connection_slot(redis_client, uuid4(), "conn-a")
    await sse_module._acquire_connection_slot(redis_client, uuid4(), "conn-b")

    with pytest.raises(RateLimitError):
        await sse_module._acquire_connection_slot(redis_client, uuid4(), "conn-c")


# --- HTTP-level: only outcomes that complete without entering the stream -----------


async def test_stream_requires_auth(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/notifications/stream")

    assert response.status_code == 401


async def test_stream_rejects_when_per_user_limit_already_reached(
    client: httpx.AsyncClient, redis_client: Redis, user: User
) -> None:
    for i in range(5):
        await sse_module._acquire_connection_slot(redis_client, user.id, f"conn-{i}")

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


# --- the failure this design replaced -------------------------------------------------


async def test_slot_left_behind_by_a_killed_process_is_reclaimed(
    redis_client: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deploy kills the process with streams open, so `finally` never runs.

    Under the previous counter this was unrecoverable: the count stayed high
    on a key with no expiry, and five such deploys locked the user out of
    their own notifications until somebody flushed Redis by hand. Scoring by
    last-seen means nobody has to notice.
    """
    user_id = uuid4()
    for i in range(5):
        await sse_module._acquire_connection_slot(redis_client, user_id, f"orphan-{i}")

    # Nothing released them -- the process is simply gone.
    with pytest.raises(RateLimitError):
        await sse_module._acquire_connection_slot(redis_client, user_id, "new-tab")

    # Once they stop checking in, their slots come back on their own.
    later = time.time() + sse_module._STALE_AFTER_SECONDS + 1
    monkeypatch.setattr(sse_module.time, "time", lambda: later)
    await sse_module._acquire_connection_slot(redis_client, user_id, "new-tab")

    assert await redis_client.zcard(sse_module._user_connections_key(user_id)) == 1


async def test_a_live_stream_keeps_its_slot_across_the_staleness_window(
    redis_client: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half: checking in must actually protect a slot, or a busy
    stream would lose its place to its own keepalive interval."""
    user_id = uuid4()
    await sse_module._acquire_connection_slot(redis_client, user_id, "long-lived")

    later = time.time() + sse_module._STALE_AFTER_SECONDS + 1
    monkeypatch.setattr(sse_module.time, "time", lambda: later)
    await sse_module._touch_connection_slot(redis_client, user_id, "long-lived")

    # A fresh acquire prunes first; the touched slot must survive that pass.
    await sse_module._acquire_connection_slot(redis_client, user_id, "second-tab")

    held = await redis_client.zrange(sse_module._user_connections_key(user_id), 0, -1)
    assert sorted(m.decode() if isinstance(m, bytes) else m for m in held) == [
        "long-lived",
        "second-tab",
    ]


async def test_releasing_one_connection_leaves_the_others_alone(redis_client: Redis) -> None:
    """A counter could not tell whose slot it was giving back."""
    user_id = uuid4()
    await sse_module._acquire_connection_slot(redis_client, user_id, "tab-a")
    await sse_module._acquire_connection_slot(redis_client, user_id, "tab-b")

    await sse_module._release_connection_slot(redis_client, user_id, "tab-a")

    held = await redis_client.zrange(sse_module._user_connections_key(user_id), 0, -1)
    assert [m.decode() if isinstance(m, bytes) else m for m in held] == ["tab-b"]
    assert await redis_client.zcard(sse_module._TOTAL_CONNECTIONS_KEY) == 1


async def test_a_double_release_cannot_free_somebody_elses_slot(redis_client: Redis) -> None:
    """The counter's worst property: decrementing twice took a slot from
    whoever held it next, and nothing detected that."""
    user_id = uuid4()
    await sse_module._acquire_connection_slot(redis_client, user_id, "tab-a")
    await sse_module._acquire_connection_slot(redis_client, user_id, "tab-b")

    await sse_module._release_connection_slot(redis_client, user_id, "tab-a")
    await sse_module._release_connection_slot(redis_client, user_id, "tab-a")

    assert await redis_client.zcard(sse_module._user_connections_key(user_id)) == 1


async def test_upgrading_over_the_old_counter_keys_does_not_break(redis_client: Redis) -> None:
    """The keys these replaced were plain counters, and Redis answers ZCARD
    against a string with WRONGTYPE.

    Deploying the sorted-set change over live keys therefore broke every
    stream request until somebody flushed Redis by hand -- a change whose
    whole purpose was surviving a deploy, unable to survive its own. The
    version segment in the key name is what makes the upgrade a non-event.
    """
    user_id = uuid4()
    # Exactly what the previous release left behind.
    await redis_client.set(f"notif:sse:conns:{user_id}", 3)
    await redis_client.set("notif:sse:conns:total", 3)

    await sse_module._acquire_connection_slot(redis_client, user_id, "after-upgrade")

    assert await redis_client.zcard(sse_module._user_connections_key(user_id)) == 1
