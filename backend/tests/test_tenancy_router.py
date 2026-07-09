"""
Verifies T-027 (tenancy router): the full HTTP surface for organizations,
memberships, and invitations under /api/v1 -- in particular that every
/orgs/{org_id}/... route resolves membership from the *path* org id, not
the X-Org-ID header app.core.dependencies uses elsewhere, so a member of
one org gets 403 (or 404 for a cross-org membership id) reaching into
another org's resource rather than silently falling through.

Same real-Postgres + real-Redis testcontainers / httpx.AsyncClient(
ASGITransport(...)) pattern as test_auth_router.py -- see that file's
docstring for why TestClient is avoided.
"""

from collections.abc import AsyncGenerator, Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport
from limits.storage import RedisStorage, storage_from_string
from limits.strategies import FixedWindowRateLimiter
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

from app.core.db import get_session
from app.core.dependencies import get_redis
from app.main import app
from app.middleware import rate_limit as rate_limit_module
from app.modules.auth.models import User
from app.modules.auth.permissions import MemberRole
from app.modules.auth.utils import create_access_token
from app.modules.billing.models import Plan
from app.modules.notifications.models import Notification
from app.modules.tenancy.models import Invitation, Membership, Organization

_TABLES = [
    User.__table__,
    Plan.__table__,
    Organization.__table__,
    Membership.__table__,
    Invitation.__table__,
    Notification.__table__,
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
    # Same swap-the-singleton's-storage approach as test_auth_router.py: the
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


@pytest.fixture
async def org_a(session: AsyncSession, user_a: User) -> Organization:
    org = Organization(name="Org A", slug=f"org-a-{uuid4().hex[:8]}")
    session.add(org)
    await session.flush()
    session.add(Membership(user_id=user_a.id, org_id=org.id, role=MemberRole.OWNER))
    await session.commit()
    return org


@pytest.fixture
async def org_b(session: AsyncSession, user_b: User) -> Organization:
    org = Organization(name="Org B", slug=f"org-b-{uuid4().hex[:8]}")
    session.add(org)
    await session.flush()
    session.add(Membership(user_id=user_b.id, org_id=org.id, role=MemberRole.OWNER))
    await session.commit()
    return org


async def _add_member(
    session: AsyncSession, org: Organization, *, role: MemberRole = MemberRole.MEMBER
) -> tuple[User, Membership]:
    user = User(email=f"member-{uuid4()}@example.com")
    session.add(user)
    await session.flush()
    membership = Membership(user_id=user.id, org_id=org.id, role=role)
    session.add(membership)
    await session.commit()
    return user, membership


async def _membership_for(session: AsyncSession, user: User, org: Organization) -> Membership:
    result = await session.execute(
        select(Membership).where(Membership.user_id == user.id, Membership.org_id == org.id)
    )
    return result.scalar_one()


def _auth_header(user: User) -> dict[str, str]:
    token = create_access_token(user.id, None, ())
    return {"Authorization": f"Bearer {token}"}


def _stale_auth_header(user: User) -> dict[str, str]:
    # T-218: an access token whose auth_time is well outside
    # require_recent_auth's window -- simulates a long-idle-but-still-valid
    # session (silent refresh keeps minting new access tokens without ever
    # re-verifying credentials, so auth_time doesn't move with them).
    stale_auth_time = datetime.now(UTC) - timedelta(minutes=30)
    token = create_access_token(user.id, None, (), auth_time=stale_auth_time)
    return {"Authorization": f"Bearer {token}"}


# --- POST /orgs ------------------------------------------------------------------


async def test_create_org_requires_auth(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/orgs", json={"name": "Acme"})

    assert response.status_code == 401


async def test_create_org_happy_path_creates_org_and_owner_membership(
    client: httpx.AsyncClient, user_a: User
) -> None:
    response = await client.post(
        "/api/v1/orgs", json={"name": "Acme Corp"}, headers=_auth_header(user_a)
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["name"] == "Acme Corp"
    assert data["slug"] == "acme-corp"


# --- GET /orgs -------------------------------------------------------------------


async def test_list_my_orgs_returns_only_orgs_user_belongs_to(
    client: httpx.AsyncClient,
    user_a: User,
    org_a: Organization,
    org_b: Organization,  # noqa: ARG001 — exists only so user_a's org list has something to exclude
) -> None:
    response = await client.get("/api/v1/orgs", headers=_auth_header(user_a))

    assert response.status_code == 200
    ids = [o["id"] for o in response.json()["data"]]
    assert ids == [str(org_a.id)]


async def test_list_my_orgs_requires_auth(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/orgs")

    assert response.status_code == 401


# --- GET /orgs/{org_id} -----------------------------------------------------------


async def test_get_org_member_returns_200(
    client: httpx.AsyncClient, user_a: User, org_a: Organization
) -> None:
    response = await client.get(f"/api/v1/orgs/{org_a.id}", headers=_auth_header(user_a))

    assert response.status_code == 200
    assert response.json()["data"]["id"] == str(org_a.id)


async def test_get_org_non_member_returns_403(
    client: httpx.AsyncClient, user_a: User, org_b: Organization
) -> None:
    response = await client.get(f"/api/v1/orgs/{org_b.id}", headers=_auth_header(user_a))

    assert response.status_code == 403


async def test_get_org_unknown_org_returns_403(client: httpx.AsyncClient, user_a: User) -> None:
    response = await client.get(f"/api/v1/orgs/{uuid4()}", headers=_auth_header(user_a))

    assert response.status_code == 403


# --- PATCH /orgs/{org_id} ---------------------------------------------------------


async def test_update_org_owner_can_rename(
    client: httpx.AsyncClient, user_a: User, org_a: Organization
) -> None:
    response = await client.patch(
        f"/api/v1/orgs/{org_a.id}", json={"name": "Renamed"}, headers=_auth_header(user_a)
    )

    assert response.status_code == 200
    assert response.json()["data"]["name"] == "Renamed"


async def test_update_org_member_without_settings_manage_returns_403(
    client: httpx.AsyncClient, session: AsyncSession, org_a: Organization
) -> None:
    member, _ = await _add_member(session, org_a)

    response = await client.patch(
        f"/api/v1/orgs/{org_a.id}", json={"name": "Renamed"}, headers=_auth_header(member)
    )

    assert response.status_code == 403


async def test_update_org_cross_tenant_returns_403(
    client: httpx.AsyncClient, user_a: User, org_b: Organization
) -> None:
    response = await client.patch(
        f"/api/v1/orgs/{org_b.id}", json={"name": "Hijacked"}, headers=_auth_header(user_a)
    )

    assert response.status_code == 403


# --- GET /orgs/{org_id}/members ---------------------------------------------------


async def test_list_members_member_can_read(
    client: httpx.AsyncClient, user_a: User, org_a: Organization
) -> None:
    response = await client.get(f"/api/v1/orgs/{org_a.id}/members", headers=_auth_header(user_a))

    assert response.status_code == 200
    assert len(response.json()["data"]) == 1


async def test_list_members_cross_tenant_returns_403(
    client: httpx.AsyncClient, user_a: User, org_b: Organization
) -> None:
    response = await client.get(f"/api/v1/orgs/{org_b.id}/members", headers=_auth_header(user_a))

    assert response.status_code == 403


# --- PATCH /orgs/{org_id}/members/{membership_id} ----------------------------------


async def test_update_member_role_member_cannot_manage(
    client: httpx.AsyncClient, session: AsyncSession, org_a: Organization
) -> None:
    member, target = await _add_member(session, org_a)

    response = await client.patch(
        f"/api/v1/orgs/{org_a.id}/members/{target.id}",
        json={"role": "admin"},
        headers=_auth_header(member),
    )

    assert response.status_code == 403


async def test_update_member_role_owner_can_promote(
    client: httpx.AsyncClient, user_a: User, session: AsyncSession, org_a: Organization
) -> None:
    _, target = await _add_member(session, org_a)

    response = await client.patch(
        f"/api/v1/orgs/{org_a.id}/members/{target.id}",
        json={"role": "admin"},
        headers=_auth_header(user_a),
    )

    assert response.status_code == 200
    assert response.json()["data"]["role"] == "admin"


async def test_update_member_role_cross_tenant_target_returns_404(  # noqa: PLR0913 — needs both orgs' users
    client: httpx.AsyncClient,
    session: AsyncSession,
    user_a: User,
    org_a: Organization,
    user_b: User,
    org_b: Organization,
) -> None:
    target = await _membership_for(session, user_b, org_b)

    response = await client.patch(
        f"/api/v1/orgs/{org_a.id}/members/{target.id}",
        json={"role": "admin"},
        headers=_auth_header(user_a),
    )

    assert response.status_code == 404


# --- DELETE /orgs/{org_id}/members/{membership_id} ---------------------------------


async def test_remove_member_member_cannot_manage(
    client: httpx.AsyncClient, session: AsyncSession, org_a: Organization
) -> None:
    member, target = await _add_member(session, org_a)

    response = await client.delete(
        f"/api/v1/orgs/{org_a.id}/members/{target.id}", headers=_auth_header(member)
    )

    assert response.status_code == 403


async def test_remove_member_owner_can_remove(
    client: httpx.AsyncClient, user_a: User, session: AsyncSession, org_a: Organization
) -> None:
    _, target = await _add_member(session, org_a)

    response = await client.delete(
        f"/api/v1/orgs/{org_a.id}/members/{target.id}", headers=_auth_header(user_a)
    )

    assert response.status_code == 200
    members = await client.get(f"/api/v1/orgs/{org_a.id}/members", headers=_auth_header(user_a))
    assert len(members.json()["data"]) == 1


async def test_remove_member_last_owner_returns_409(
    client: httpx.AsyncClient, user_a: User, session: AsyncSession, org_a: Organization
) -> None:
    owner_membership = await _membership_for(session, user_a, org_a)

    response = await client.delete(
        f"/api/v1/orgs/{org_a.id}/members/{owner_membership.id}", headers=_auth_header(user_a)
    )

    assert response.status_code == 409


async def test_remove_member_without_recent_auth_returns_403(
    client: httpx.AsyncClient, user_a: User, session: AsyncSession, org_a: Organization
) -> None:
    _, target = await _add_member(session, org_a)

    response = await client.delete(
        f"/api/v1/orgs/{org_a.id}/members/{target.id}", headers=_stale_auth_header(user_a)
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "step_up_auth_required"


async def test_remove_member_cross_tenant_target_returns_404(  # noqa: PLR0913 — needs both orgs' users
    client: httpx.AsyncClient,
    session: AsyncSession,
    user_a: User,
    org_a: Organization,
    user_b: User,
    org_b: Organization,
) -> None:
    target = await _membership_for(session, user_b, org_b)

    response = await client.delete(
        f"/api/v1/orgs/{org_a.id}/members/{target.id}", headers=_auth_header(user_a)
    )

    assert response.status_code == 404


# --- POST /orgs/{org_id}/invitations ------------------------------------------------


async def test_create_invitation_member_without_permission_returns_403(
    client: httpx.AsyncClient, session: AsyncSession, org_a: Organization
) -> None:
    member, _ = await _add_member(session, org_a)

    response = await client.post(
        f"/api/v1/orgs/{org_a.id}/invitations",
        json={"email": "new@example.com", "role": "member"},
        headers=_auth_header(member),
    )

    assert response.status_code == 403


# --- GET /invitations/{token} -------------------------------------------------------


async def test_preview_invitation_invalid_token_returns_401(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/invitations/not-a-real-token")

    assert response.status_code == 401


# --- POST /invitations/{token}/accept -----------------------------------------------


async def test_accept_invitation_invalid_token_returns_401(
    client: httpx.AsyncClient, user_a: User
) -> None:
    response = await client.post(
        "/api/v1/invitations/not-a-real-token/accept", headers=_auth_header(user_a)
    )

    assert response.status_code == 401


async def test_accept_invitation_requires_auth(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/invitations/some-token/accept")

    assert response.status_code == 401


# --- full invitation round-trip via HTTP --------------------------------------------


async def test_invitation_full_round_trip_via_http(
    client: httpx.AsyncClient,
    user_a: User,
    org_a: Organization,
    user_b: User,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    invite_response = await client.post(
        f"/api/v1/orgs/{org_a.id}/invitations",
        json={"email": user_b.email, "role": "member"},
        headers=_auth_header(user_a),
    )
    assert invite_response.status_code == 200
    assert invite_response.json()["data"] is None

    # Not enqueued_jobs[-1]: invite() also notifies the inviter (T-160's
    # member_invited notification), which enqueues its own send_email job
    # after this one.
    function, kwargs = next(
        (f, k) for f, k in enqueued_jobs if f == "send_email" and k["to"] == user_b.email
    )
    assert function == "send_email"
    token = kwargs["context"]["accept_url"].rsplit("/", 1)[-1]

    preview_response = await client.get(f"/api/v1/invitations/{token}")
    assert preview_response.status_code == 200
    preview = preview_response.json()["data"]
    assert preview["org_name"] == org_a.name
    assert preview["email"] == user_b.email
    assert preview["role"] == "member"

    accept_response = await client.post(
        f"/api/v1/invitations/{token}/accept", headers=_auth_header(user_b)
    )
    assert accept_response.status_code == 200
    accepted = accept_response.json()["data"]
    assert accepted["org_id"] == str(org_a.id)
    assert accepted["role"] == "member"

    members_response = await client.get(
        f"/api/v1/orgs/{org_a.id}/members", headers=_auth_header(user_a)
    )
    assert len(members_response.json()["data"]) == 2

    second_accept = await client.post(
        f"/api/v1/invitations/{token}/accept", headers=_auth_header(user_b)
    )
    assert second_accept.status_code == 401
