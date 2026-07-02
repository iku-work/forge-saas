import asyncio
import os
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from dotenv import dotenv_values
from httpx import ASGITransport
from limits.storage import RedisStorage, storage_from_string
from limits.strategies import FixedWindowRateLimiter
from redis.asyncio import Redis
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import Session as SyncSession
from sqlalchemy.orm import SessionTransaction
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from app.core import encrypted_type
from app.core import jobs as jobs_module
from app.core.config import get_settings
from app.core.db import get_session
from app.core.dependencies import get_redis
from app.core.migrations import build_config
from app.core.migrations import upgrade as upgrade_migrations
from app.main import app
from app.middleware import rate_limit as rate_limit_module
from app.modules.auth.permissions import MemberRole
from app.modules.auth.repository import UserRepository
from app.modules.auth.utils import create_access_token, hash_password
from app.modules.tenancy.repository import MembershipRepository, OrganizationRepository

_ENV_TEST_FILE = Path(__file__).parent.parent / ".env.test"
_AUTHED_PASSWORD = "Correct-Horse9"


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in dotenv_values(_ENV_TEST_FILE).items():
        if value is not None:
            monkeypatch.setenv(key, value)
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_fernet_cache() -> Generator[None]:
    # EncryptedString caches a MultiFernet singleton at module scope (see
    # app.core.encrypted_type._get_fernet) — without resetting it, a test that
    # changes ENCRYPTION_KEY would leak a stale Fernet instance into the next.
    encrypted_type._fernet_instance = None
    yield
    encrypted_type._fernet_instance = None


@pytest.fixture(autouse=True)
def enqueued_jobs(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    # AuthService/TenancyService/GDPRService enqueue verification/reset/
    # invitation emails and export/purge jobs through app.core.jobs.enqueue_job
    # (looked up dynamically as `jobs.enqueue_job` at call time, so patching
    # the attribute here reaches every caller without touching a real Redis).
    calls: list[tuple[str, dict[str, Any]]] = []

    async def _fake_enqueue_job(
        function: str,
        **kwargs: Any,  # noqa: ANN401 -- mirrors app.core.jobs.enqueue_job's **kwargs
    ) -> None:
        calls.append((function, kwargs))

    monkeypatch.setattr(jobs_module, "enqueue_job", _fake_enqueue_job)
    return calls


# T-048: shared real-Postgres + real-Redis fixtures so every later test file
# can get a working DB/HTTP client without rolling its own testcontainers
# setup. One container pair for the whole pytest session (scope="session") --
# only the per-test `session` fixture below tears down/rolls back state.


@pytest.fixture(scope="session")
def postgres_container() -> Generator[PostgresContainer, None, None]:
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def redis_container() -> Generator[RedisContainer, None, None]:
    with RedisContainer("redis:7-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def engine(postgres_container: PostgresContainer) -> Generator[AsyncEngine, None, None]:
    url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    # alembic/env.py sources the DB URL from get_settings(), never from an
    # ini file (§0.4 law) -- monkeypatch isn't usable at session scope, so
    # the env var is set directly; nothing downstream depends on it once the
    # migrations below have run, since create_async_engine() bakes the URL
    # in at construction time.
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    upgrade_migrations(build_config())

    # NullPool: pytest-asyncio gives every test function its own event loop,
    # and an asyncpg connection is bound to whichever loop created it. A
    # session-scoped engine that pooled connections across tests would hand
    # a later test a connection bound to an earlier test's (closed) loop.
    # NullPool opens a fresh connection per checkout, so the engine itself
    # can safely outlive any single test.
    db_engine = create_async_engine(url, poolclass=NullPool)
    try:
        yield db_engine
    finally:
        asyncio.run(db_engine.dispose())


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    # Standard SQLAlchemy test pattern: bind the Session to a Connection that
    # already has a transaction + savepoint open. Application code (routers)
    # calling session.commit() only ends the savepoint -- the listener below
    # immediately opens a new one -- so the outer transaction, rolled back
    # at the end, undoes everything regardless of how many commits happened
    # in between. DB is pristine after every test.
    async with engine.connect() as conn:
        await conn.begin()
        await conn.begin_nested()
        test_session = AsyncSession(bind=conn, expire_on_commit=False)

        @event.listens_for(test_session.sync_session, "after_transaction_end")
        def _restart_savepoint(
            sync_session: SyncSession,  # noqa: ARG001 — required listener signature
            transaction: SessionTransaction,  # noqa: ARG001 — required listener signature
        ) -> None:
            if conn.closed:
                return
            sync_conn = conn.sync_connection
            assert sync_conn is not None  # always set once engine.connect() has returned
            if not sync_conn.in_nested_transaction():
                sync_conn.begin_nested()

        yield test_session

        await test_session.close()
        await conn.rollback()


@pytest.fixture
def _rate_limit_storage(redis_container: RedisContainer) -> Generator[RedisStorage, None, None]:
    # Same swap-the-singleton's-storage approach as test_rate_limit.py /
    # test_auth_router.py: auth_limit/sensitive_limit/api_limit close over
    # the one `limiter` built at import time, so any router hit through
    # `client` needs its storage pointed at a reachable, disposable Redis.
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
async def _redis_client(redis_container: RedisContainer) -> AsyncGenerator[Redis, None]:
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    redis_conn: Redis = Redis.from_url(f"redis://{host}:{port}/0")
    await redis_conn.flushdb()
    yield redis_conn
    await redis_conn.aclose()


@pytest.fixture
async def client(
    session: AsyncSession,
    _redis_client: Redis,
    _rate_limit_storage: RedisStorage,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    _rate_limit_storage.reset()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_redis] = lambda: _redis_client
    try:
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            # https:// base_url so httpx's cookie jar forwards Secure-marked
            # cookies (the refresh_token cookie) back on later requests.
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as c:
                yield c
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
async def authed_client(client: httpx.AsyncClient, session: AsyncSession) -> httpx.AsyncClient:
    user = await UserRepository(session).create(
        email="owner@example.com",
        hashed_password=hash_password(_AUTHED_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    org = await OrganizationRepository(session).create(name="Acme", slug=f"acme-{uuid4()}")
    await MembershipRepository(session).create(
        user_id=user.id, org_id=org.id, role=MemberRole.OWNER
    )
    await session.commit()

    token = create_access_token(user.id, None, ())
    client.headers["Authorization"] = f"Bearer {token}"
    client.headers["X-Org-ID"] = str(org.id)
    return client
