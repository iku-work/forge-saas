"""
Verifies T-033 (GDPR router): the full HTTP surface for data export, the
delete-me flow, and the consent log under /api/v1/me. In particular: the
DELETE /me confirmation gate rejects anything but the exact literal "DELETE"
before any deletion happens, GET /export/{job_id} is ownership-scoped (a job
belonging to another user 404s, not 200s with someone else's data), and
POST /consents is append-only -- two calls produce two rows.

Same real-Postgres + real-Redis testcontainers / httpx.AsyncClient(
ASGITransport(...)) pattern as test_tenancy_router.py -- see that file's
docstring for why TestClient is avoided.
"""

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

from app.core.db import get_session
from app.core.dependencies import get_redis
from app.main import app
from app.middleware import rate_limit as rate_limit_module
from app.modules.audit.models import AuditEvent
from app.modules.auth.models import User
from app.modules.auth.utils import create_access_token
from app.modules.gdpr.models import ConsentRecord, DataExportJob

# T-291: this file needs gdpr enabled -- its routes, its tables, or
# both. CI's module-matrix job deselects it in the combination that turns
# every optional module off.
pytestmark = pytest.mark.module_dependent

_TABLES = [User.__table__, ConsentRecord.__table__, DataExportJob.__table__, AuditEvent.__table__]


@pytest.fixture(scope="module")
def postgres_container() -> "PostgresContainer":
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture(scope="module")
def redis_container() -> "RedisContainer":
    with RedisContainer("redis:7-alpine") as container:
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
def session_factory(test_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


# A direct handle for test setup/assertions -- separate from the sessions the
# overridden get_session() hands to each request, exactly like a second
# psql connection would be in production.
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
    # Same swap-the-singleton's-storage approach as test_tenancy_router.py: the
    # api_limit decorator closes over the one `limiter` object built at
    # import time, so pointing its storage at a disposable Redis (and
    # restoring it after) is how requests against it actually get counted.
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
async def user_a(session: AsyncSession) -> User:
    u = User(email=f"a-{uuid4()}@example.com")
    session.add(u)
    await session.flush()
    await session.commit()
    return u


@pytest.fixture
async def user_b(session: AsyncSession) -> User:
    u = User(email=f"b-{uuid4()}@example.com")
    session.add(u)
    await session.flush()
    await session.commit()
    return u


def _auth_header(user: User) -> dict[str, str]:
    token = create_access_token(user.id, None, ())
    return {"Authorization": f"Bearer {token}"}


# --- GET /me/export ----------------------------------------------------------------


async def test_request_export_requires_auth(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/me/export")

    assert response.status_code == 401


async def test_request_export_happy_path_returns_202_with_job_id(
    client: httpx.AsyncClient, user_a: User
) -> None:
    response = await client.get("/api/v1/me/export", headers=_auth_header(user_a))

    assert response.status_code == 202
    assert "job_id" in response.json()["data"]


# --- GET /me/export/{job_id} -------------------------------------------------------


async def test_get_export_job_happy_path_returns_pending_status(
    client: httpx.AsyncClient, user_a: User
) -> None:
    created = await client.get("/api/v1/me/export", headers=_auth_header(user_a))
    job_id = created.json()["data"]["job_id"]

    response = await client.get(f"/api/v1/me/export/{job_id}", headers=_auth_header(user_a))

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "pending"


async def test_get_export_job_cross_user_returns_404(
    client: httpx.AsyncClient, user_a: User, user_b: User
) -> None:
    created = await client.get("/api/v1/me/export", headers=_auth_header(user_a))
    job_id = created.json()["data"]["job_id"]

    response = await client.get(f"/api/v1/me/export/{job_id}", headers=_auth_header(user_b))

    assert response.status_code == 404


async def test_get_export_job_unknown_id_returns_404(
    client: httpx.AsyncClient, user_a: User
) -> None:
    response = await client.get(f"/api/v1/me/export/{uuid4()}", headers=_auth_header(user_a))

    assert response.status_code == 404


# --- DELETE /me ----------------------------------------------------------------------


async def test_delete_me_requires_auth(client: httpx.AsyncClient) -> None:
    response = await client.request("DELETE", "/api/v1/me", json={"confirm": "DELETE"})

    assert response.status_code == 401


async def test_delete_me_missing_confirm_returns_422(
    client: httpx.AsyncClient, user_a: User
) -> None:
    response = await client.request("DELETE", "/api/v1/me", json={}, headers=_auth_header(user_a))

    assert response.status_code == 422


async def test_delete_me_wrong_confirm_value_returns_422(
    client: httpx.AsyncClient, user_a: User
) -> None:
    response = await client.request(
        "DELETE", "/api/v1/me", json={"confirm": "delete"}, headers=_auth_header(user_a)
    )

    assert response.status_code == 422


async def test_delete_me_happy_path_returns_202_and_soft_deletes_user(
    client: httpx.AsyncClient, user_a: User, session: AsyncSession
) -> None:
    response = await client.request(
        "DELETE", "/api/v1/me", json={"confirm": "DELETE"}, headers=_auth_header(user_a)
    )

    assert response.status_code == 202
    # The request was committed on its own session (see get_session_override);
    # this fixture's session still has the pre-delete row in its identity map
    # (expire_on_commit=False), so a plain re-select would return the stale
    # cached copy instead of hitting the database again.
    await session.refresh(user_a)
    assert user_a.deleted_at is not None


# --- GET /me/consents ----------------------------------------------------------------


async def test_list_consents_requires_auth(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/me/consents")

    assert response.status_code == 401


async def test_list_consents_empty_for_new_user(client: httpx.AsyncClient, user_a: User) -> None:
    response = await client.get("/api/v1/me/consents", headers=_auth_header(user_a))

    assert response.status_code == 200
    assert response.json()["data"] == []


# --- POST /me/consents ---------------------------------------------------------------


async def test_record_consent_missing_fields_returns_422(
    client: httpx.AsyncClient, user_a: User
) -> None:
    response = await client.post(
        "/api/v1/me/consents", json={"granted": True}, headers=_auth_header(user_a)
    )

    assert response.status_code == 422


async def test_record_consent_called_twice_creates_two_rows(
    client: httpx.AsyncClient, user_a: User
) -> None:
    payload = {"consent_type": "marketing", "granted": True, "version": "2026-06-01"}

    first = await client.post("/api/v1/me/consents", json=payload, headers=_auth_header(user_a))
    second = await client.post(
        "/api/v1/me/consents",
        json={**payload, "granted": False},
        headers=_auth_header(user_a),
    )

    assert first.status_code == 200
    assert second.status_code == 200

    listed = await client.get("/api/v1/me/consents", headers=_auth_header(user_a))
    assert len(listed.json()["data"]) == 2
