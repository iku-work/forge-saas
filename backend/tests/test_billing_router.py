"""
Verifies T-031's billing router: checkout-session and portal-session both
require BILLING_MANAGE, subscription requires BILLING_READ, plans is public,
and every gated route resolves its org from the X-Org-ID header (app.core.
dependencies' convention) rather than a path parameter, since none of these
routes have an org_id segment.

stripe.checkout.Session.create_async / stripe.billing_portal.Session.create_async /
stripe.Customer.create_async are monkeypatched (T-206 switched these call
sites onto stripe's `_async` method variants, see app.modules.billing.service)
-- this file verifies routing, auth/permission gating, and envelope shape,
not Stripe's request/response contract (see test_billing_checkout.py for
that). Same real-Postgres + real-Redis testcontainers /
httpx.AsyncClient(ASGITransport(...)) pattern as test_tenancy_router.py --
see that file's docstring for why TestClient is avoided.
"""

from collections.abc import AsyncGenerator, Callable, Iterator
from uuid import uuid4

import httpx
import pytest
import stripe
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
from app.modules.auth.models import User
from app.modules.auth.permissions import MemberRole
from app.modules.auth.utils import create_access_token
from app.modules.billing.models import Plan, Subscription, WebhookEvent
from app.modules.tenancy.models import Membership, Organization

_TABLES = [
    User.__table__,
    Plan.__table__,
    Subscription.__table__,
    WebhookEvent.__table__,
    Organization.__table__,
    Membership.__table__,
]


@pytest.fixture(scope="module")
def postgres_container() -> "PostgresContainer":
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture(scope="module")
def redis_container() -> "RedisContainer":
    with RedisContainer("redis:7-alpine") as container:
        yield container


# Function-scoped (not module-scoped): see test_tenancy_router.py -- an
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
    # Same swap-the-singleton's-storage approach as test_tenancy_router.py:
    # the api_limit decorator closes over the one `limiter` object built at
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
async def owner(session: AsyncSession) -> User:
    u = User(email=f"owner-{uuid4()}@example.com")
    session.add(u)
    await session.flush()
    await session.commit()
    return u


@pytest.fixture
async def org(session: AsyncSession, owner: User) -> Organization:
    organization = Organization(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    session.add(organization)
    await session.flush()
    session.add(Membership(user_id=owner.id, org_id=organization.id, role=MemberRole.OWNER))
    await session.commit()
    return organization


async def _add_member(
    session: AsyncSession, org: Organization, *, role: MemberRole = MemberRole.MEMBER
) -> User:
    user = User(email=f"member-{uuid4()}@example.com")
    session.add(user)
    await session.flush()
    session.add(Membership(user_id=user.id, org_id=org.id, role=role))
    await session.commit()
    return user


async def _make_plan(session: AsyncSession, *, is_active: bool = True) -> Plan:
    plan = Plan(
        name="Pro",
        slug=f"pro-{uuid4().hex[:8]}",
        stripe_price_id_monthly="price_monthly_1",
        stripe_price_id_yearly="price_yearly_1",
        price_monthly=2900,
        price_yearly=29000,
        features={"seats": 5},
        is_active=is_active,
    )
    session.add(plan)
    await session.flush()
    await session.commit()
    return plan


def _headers(user: User, org: Organization) -> dict[str, str]:
    token = create_access_token(user.id, None, ())
    return {"Authorization": f"Bearer {token}", "X-Org-ID": str(org.id)}


class _FakeCustomer:
    def __init__(self, customer_id: str) -> None:
        self.id = customer_id


class _FakeCheckoutSession:
    def __init__(self, url: str) -> None:
        self.url = url


class _FakePortalSession:
    def __init__(self, url: str) -> None:
        self.url = url


# --- POST /billing/checkout-session ------------------------------------------------


async def test_checkout_session_requires_auth(client: httpx.AsyncClient, org: Organization) -> None:
    response = await client.post(
        "/api/v1/billing/checkout-session",
        json={"plan_slug": "pro", "interval": "monthly"},
        headers={"X-Org-ID": str(org.id)},
    )

    assert response.status_code == 401


async def test_checkout_session_requires_org_header(client: httpx.AsyncClient, owner: User) -> None:
    token = create_access_token(owner.id, None, ())
    response = await client.post(
        "/api/v1/billing/checkout-session",
        json={"plan_slug": "pro", "interval": "monthly"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400


async def test_checkout_session_member_without_billing_manage_returns_403(
    client: httpx.AsyncClient, session: AsyncSession, org: Organization
) -> None:
    member = await _add_member(session, org)

    response = await client.post(
        "/api/v1/billing/checkout-session",
        json={"plan_slug": "pro", "interval": "monthly"},
        headers=_headers(member, org),
    )

    assert response.status_code == 403


async def test_checkout_session_owner_happy_path(
    client: httpx.AsyncClient,
    session: AsyncSession,
    owner: User,
    org: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = await _make_plan(session)

    async def _customer_create(**_kwargs: object) -> _FakeCustomer:
        return _FakeCustomer("cus_1")

    async def _checkout_create(**_kwargs: object) -> _FakeCheckoutSession:
        return _FakeCheckoutSession(url="https://checkout.stripe.com/test")

    monkeypatch.setattr(stripe.Customer, "create_async", _customer_create)
    monkeypatch.setattr(stripe.checkout.Session, "create_async", _checkout_create)

    response = await client.post(
        "/api/v1/billing/checkout-session",
        json={"plan_slug": plan.slug, "interval": "monthly"},
        headers=_headers(owner, org),
    )

    assert response.status_code == 200
    assert response.json()["data"]["url"] == "https://checkout.stripe.com/test"


async def test_checkout_session_unknown_plan_returns_404(
    client: httpx.AsyncClient,
    owner: User,
    org: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _customer_create(**_kwargs: object) -> _FakeCustomer:
        return _FakeCustomer("cus_1")

    monkeypatch.setattr(stripe.Customer, "create_async", _customer_create)

    response = await client.post(
        "/api/v1/billing/checkout-session",
        json={"plan_slug": "does-not-exist", "interval": "monthly"},
        headers=_headers(owner, org),
    )

    assert response.status_code == 404


# --- POST /billing/portal-session --------------------------------------------------


async def test_portal_session_requires_auth(client: httpx.AsyncClient, org: Organization) -> None:
    response = await client.post(
        "/api/v1/billing/portal-session", headers={"X-Org-ID": str(org.id)}
    )

    assert response.status_code == 401


async def test_portal_session_member_without_billing_manage_returns_403(
    client: httpx.AsyncClient, session: AsyncSession, org: Organization
) -> None:
    member = await _add_member(session, org)

    response = await client.post("/api/v1/billing/portal-session", headers=_headers(member, org))

    assert response.status_code == 403


async def test_portal_session_owner_happy_path(
    client: httpx.AsyncClient,
    owner: User,
    org: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _customer_create(**_kwargs: object) -> _FakeCustomer:
        return _FakeCustomer("cus_1")

    async def _portal_create(**_kwargs: object) -> _FakePortalSession:
        return _FakePortalSession(url="https://billing.stripe.com/portal/test")

    monkeypatch.setattr(stripe.Customer, "create_async", _customer_create)
    monkeypatch.setattr(stripe.billing_portal.Session, "create_async", _portal_create)

    response = await client.post("/api/v1/billing/portal-session", headers=_headers(owner, org))

    assert response.status_code == 200
    assert response.json()["data"]["url"] == "https://billing.stripe.com/portal/test"


# --- GET /billing/subscription ------------------------------------------------------


async def test_get_subscription_requires_auth(client: httpx.AsyncClient, org: Organization) -> None:
    response = await client.get("/api/v1/billing/subscription", headers={"X-Org-ID": str(org.id)})

    assert response.status_code == 401


async def test_get_subscription_member_without_billing_read_returns_403(
    client: httpx.AsyncClient, session: AsyncSession, org: Organization
) -> None:
    member = await _add_member(session, org)

    response = await client.get("/api/v1/billing/subscription", headers=_headers(member, org))

    assert response.status_code == 403


async def test_get_subscription_admin_can_read(
    client: httpx.AsyncClient, session: AsyncSession, org: Organization
) -> None:
    admin = await _add_member(session, org, role=MemberRole.ADMIN)

    response = await client.get("/api/v1/billing/subscription", headers=_headers(admin, org))

    assert response.status_code == 200
    assert response.json()["data"] is None


# --- GET /billing/plans -------------------------------------------------------------


async def test_list_plans_is_public(client: httpx.AsyncClient, session: AsyncSession) -> None:
    active = await _make_plan(session)
    inactive = await _make_plan(session, is_active=False)

    response = await client.get("/api/v1/billing/plans")

    assert response.status_code == 200
    slugs = {p["slug"] for p in response.json()["data"]}
    assert active.slug in slugs
    assert inactive.slug not in slugs
