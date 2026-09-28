import asyncio
import os
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import asyncpg
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

from app.core import encrypted_type, metrics, resilience
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
from app.modules.product_settings import service as product_settings_service
from app.modules.tenancy.repository import MembershipRepository, OrganizationRepository
from tests._database import DatabaseHandle

_ENV_TEST_FILE = Path(__file__).parent.parent / ".env.test"
_AUTHED_PASSWORD = "Correct-Horse9"


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in dotenv_values(_ENV_TEST_FILE).items():
        if value is not None:
            monkeypatch.setenv(key, value)
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _fast_resilience_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    # app.core.resilience.resilient()'s tenacity backoff (0.5s -> 4s) reads
    # these two module constants as plain globals at call time, not as bound
    # defaults -- shrinking them here makes every @resilient()-wrapped retry
    # in the whole suite near-instant without changing retry *behavior*.
    monkeypatch.setattr(resilience, "_RETRY_MULTIPLIER_SECONDS", 0.001)
    monkeypatch.setattr(resilience, "_RETRY_MAX_WAIT_SECONDS", 0.002)


@pytest.fixture(autouse=True)
def _reset_breakers() -> Generator[None]:
    # app.core.resilience.BREAKERS is module-level, shared state (one
    # CircuitBreaker instance per integration, reused by every call site) --
    # reset around every test so one test tripping a breaker never leaks into
    # the next.
    resilience.reset_all_breakers()
    yield
    resilience.reset_all_breakers()


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
    # AuthService/TenancyService/GDPRService/DunningService enqueue verification/
    # reset/invitation/dunning emails and export/purge jobs through
    # app.core.jobs.enqueue_job (looked up dynamically as `jobs.enqueue_job` at
    # call time, so patching the attribute here reaches every caller without
    # touching a real Redis). Returns a synthetic job id per call (mirroring the
    # real function's `str | None` return) so callers that persist the id, like
    # DunningService.start, have something realistic to store. revoke_job is
    # also faked to a no-op for the same reason -- DunningService.cancel/
    # complete call it with those synthetic ids, which would otherwise try (and
    # fail) to reach a real Redis; see tests/test_billing_dunning_service.py
    # for the dedicated real-Redis coverage of the genuine revoke_job.
    calls: list[tuple[str, dict[str, Any]]] = []

    async def _fake_enqueue_job(
        function: str,
        **kwargs: Any,  # noqa: ANN401 -- mirrors app.core.jobs.enqueue_job's **kwargs
    ) -> str:
        calls.append((function, kwargs))
        return f"fake-job-{len(calls)}"

    async def _fake_revoke_job(job_id: str) -> None:  # noqa: ARG001 -- mirrors app.core.jobs.revoke_job's signature
        return

    monkeypatch.setattr(jobs_module, "enqueue_job", _fake_enqueue_job)
    monkeypatch.setattr(jobs_module, "revoke_job", _fake_revoke_job)
    return calls


# T-048: shared real-Postgres + real-Redis fixtures so every later test file
# can get a working DB/HTTP client without rolling its own testcontainers
# setup. One container pair for the whole pytest session (scope="session") --
# only the per-test `session` fixture below tears down/rolls back state.


@pytest.fixture(scope="session")
def _shared_postgres() -> Generator[PostgresContainer, None, None]:
    """The single Postgres server for the whole run.

    Every test file that needs its own schema used to start its own
    container: 64 of them, at fifteen to twenty seconds each, which was most
    of a fifty-two minute suite. They are not wrong to want isolation --
    each creates and drops its own subset of tables, and doing that against
    the migrated schema `engine` builds below would break every test that
    came after. But isolation is what a *database* is for, and creating one
    costs about a tenth of a second.
    """
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture(scope="module")
def postgres_container(
    _shared_postgres: PostgresContainer, request: pytest.FixtureRequest
) -> DatabaseHandle:
    """A fresh, empty database for this module, on the shared server.

    Named `postgres_container` deliberately: these files already override a
    fixture of that name, so keeping it means deleting their override and
    changing nothing else.

    Not dropped afterwards. The server is thrown away at the end of the run,
    and `DROP DATABASE` fails outright if any connection is still open --
    turning a tidy-up into a flake that fires on whichever module happens to
    leave a socket behind.
    """
    return _make_database(_shared_postgres, request.module.__name__)


@pytest.fixture
def fresh_database(
    _shared_postgres: PostgresContainer, request: pytest.FixtureRequest
) -> DatabaseHandle:
    """A new database for *this test*, for the files that need one.

    The migration and model tests run `alembic upgrade` against an empty
    schema, so one database per module would leave the second test in a file
    looking at the first one's tables. They used to buy that isolation with a
    container per test, which is the most expensive way to get it.
    """
    return _make_database(_shared_postgres, request.node.name)


def _make_database(container: PostgresContainer, label: str) -> DatabaseHandle:
    base = container.get_connection_url()
    # Postgres truncates identifiers at 63 bytes *silently*, so a long label
    # could collide with another after truncation. The uuid goes first, and
    # the label is only there to make the database list readable when
    # something has gone wrong.
    safe = "".join(c if c.isalnum() else "_" for c in label.rsplit(".", 1)[-1])
    name = f"t{uuid4().hex[:12]}_{safe}"[:63]

    # asyncpg, not a sync engine: psycopg2 is not installed here at all --
    # which is why every test file in this suite does
    # `.replace("psycopg2", "asyncpg")` on the URL testcontainers hands it.
    # `asyncio.run` from a sync fixture is the same shape the `engine`
    # fixture already uses for its teardown.
    asyncio.run(_create_database(base.replace("+psycopg2", ""), name))

    return DatabaseHandle(base.rsplit("/", 1)[0] + f"/{name}")


async def _create_database(dsn: str, name: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        # No parameter binding: CREATE DATABASE takes an identifier, not a
        # value. `name` is built above from a uuid and an alphanumeric-only
        # label, so there is nothing here that came from outside this file.
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def redis_container() -> Generator[RedisContainer, None, None]:
    with RedisContainer("redis:7-alpine") as container:
        yield container


@pytest.fixture(autouse=True)
def _reset_metrics_scrape_backoff() -> Generator[None, None, None]:
    """Un-poison `app.core.metrics`' Redis stand-down between tests.

    That window is process-global on purpose (a scrape must not pay four
    connect timeouts while Redis is down), which makes it exactly the kind of
    state that leaks across a suite: `test_gauges_fail_closed_to_zero_when_
    redis_unreachable` induces a failure deliberately, and without this the
    next thirty seconds of tests read every Redis-backed gauge as 0.0 without
    asking Redis. Alone they pass, in a full run they do not -- this
    codebase's most-repeated trap, so it gets a fixture rather than a note.
    """
    metrics.reset_scrape_backoff()
    yield
    metrics.reset_scrape_backoff()


@pytest.fixture(scope="session")
def engine(_shared_postgres: PostgresContainer) -> Generator[AsyncEngine, None, None]:
    url = _shared_postgres.get_connection_url().replace("psycopg2", "asyncpg")
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
def product_settings_session(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """T-263: point ProductSettings' non-DI database read at the test session.

    `app.modules.product_settings.service.read_settings` is called from two
    places FastAPI's dependency graph doesn't reach -- MaintenanceModeMiddleware
    (Starlette middleware runs before a single dependency is solved) and any
    test calling it directly -- so it opens its own session via
    `settings_session_scope`. Left alone that lands on the engine
    `app.core.db` built at *import* time, i.e. from backend/.env.test, before
    the `engine` fixture pointed DATABASE_URL at the testcontainer. That is the
    same import-time settings binding trap the rate limiter hit (see
    app/middleware/rate_limit.py's comment), and it shows up here as a
    connection refused to localhost:5432.

    The scope below deliberately does not close the session it yields: it is
    the test's own transaction-bound session, and closing it would take the
    savepoint machinery with it.
    """

    @asynccontextmanager
    async def _test_settings_session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    monkeypatch.setattr(product_settings_service, "settings_session_scope", _test_settings_session)


@pytest.fixture
async def client(
    session: AsyncSession,
    _redis_client: Redis,
    _rate_limit_storage: RedisStorage,
    product_settings_session: None,  # noqa: ARG001 -- see the fixture; the middleware needs it
) -> AsyncGenerator[httpx.AsyncClient, None]:
    _rate_limit_storage.reset()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_redis] = lambda: _redis_client

    try:
        async with app.router.lifespan_context(app):
            app.state.redis = _redis_client
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
