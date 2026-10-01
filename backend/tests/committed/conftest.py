"""Committed mode: each test gets its own migrated database, and real commits.

The rest of the suite runs every test inside one rolled-back transaction on a
shared database (tests/conftest.py's `session`), with the app handed that same
session. That is fast, but a test can only see what one session sees. The
files here need what production has instead: each request opens its own
session, commits for real, and a second connection sees exactly what was
committed and nothing else -- which is how they prove persistence (review R2:
security writes that a rejection used to roll back), races between requests,
and jobs that open sessions of their own.

Each test gets a fresh clone of `migrated_template`, dropped afterwards.
These fixtures override the savepoint ones of the same names for every file
in this directory; a file adds only what is specific to it.
"""

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager

import httpx
import pytest
from limits.storage import RedisStorage
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.postgres import PostgresContainer

from app.modules.product_settings import service as product_settings_service
from tests._client import serve_app
from tests._database import clone_template, drop_database


@pytest.fixture(autouse=True)
def _wire_rate_limiter(rate_limit_storage: RedisStorage) -> None:
    """Every committed test can reach a rate limit (resolving a user charges
    the shared user budget), and the limiter built at import points at
    .env.test's address."""


@pytest.fixture
async def test_engine(
    _shared_postgres: PostgresContainer,  # noqa: PT019 -- the value is used: it is the server
    migrated_template: str,
) -> AsyncGenerator[AsyncEngine, None]:
    url = await clone_template(_shared_postgres, migrated_template)
    # A real pool, per test: tests that prove a job holds no connection
    # during I/O read `engine.pool.checkedout()`.
    engine = create_async_engine(url)
    try:
        yield engine
    finally:
        await engine.dispose()
        await drop_database(_shared_postgres, url)


@pytest.fixture
def session_factory(test_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """The test's own connection, for setup and assertions -- separate from
    the ones requests get, as a psql session would be in production."""
    async with session_factory() as s:
        yield s


@pytest.fixture
def get_session_override(
    session_factory: async_sessionmaker[AsyncSession],
) -> Callable[[], AsyncGenerator[AsyncSession, None]]:
    """`get_session` as production runs it: a session per request, rolled
    back when the request raises."""

    async def _get_session() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as request_session:
            try:
                yield request_session
            except Exception:
                await request_session.rollback()
                raise

    return _get_session


@pytest.fixture
def product_settings_session(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The committed counterpart of tests/conftest.py's fixture of the same
    name: the maintenance middleware's own session comes from this test's
    database, not from the engine `app.core.db` built at import."""

    @asynccontextmanager
    async def _scope() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as own_session:
            yield own_session

    monkeypatch.setattr(product_settings_service, "settings_session_scope", _scope)


@pytest.fixture
async def client(
    get_session_override: Callable[[], AsyncGenerator[AsyncSession, None]],
    redis_client: Redis,
    product_settings_session: None,  # noqa: ARG001 -- see the fixture; the middleware needs it
) -> AsyncGenerator[httpx.AsyncClient, None]:
    async with serve_app(
        get_session_override=get_session_override,
        redis=redis_client,
    ) as c:
        yield c
