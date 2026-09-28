"""
Verifies T-133's GET /api/v1/billing/invoices: requires auth + the X-Org-ID
header, gates on BILLING_READ (not BILLING_MANAGE -- read-only history),
resolves the Stripe customer from the org the X-Org-ID header names (never
another org's, even if both exist in the same request's DB), and surfaces
Stripe's `has_more` as `meta.next_cursor` per the cursor pagination
convention (§0.3) -- Stripe's own invoice id, not this repo's internal
(created_at, id) cursor.

stripe.Invoice.list_async is monkeypatched (T-206 switched this call site
onto stripe's `_async` method variant, see app.modules.billing.invoices).
Same real-Postgres + real-Redis testcontainers /
httpx.AsyncClient(ASGITransport(...)) pattern as test_billing_router.py --
see that file's docstring for why TestClient is avoided.
"""

from collections.abc import AsyncGenerator, Callable, Iterator
from types import SimpleNamespace
from typing import Any
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
from testcontainers.redis import RedisContainer

from app.core.db import get_session
from app.core.dependencies import get_redis
from app.main import app
from app.middleware import rate_limit as rate_limit_module
from app.modules.auth.models import User
from app.modules.auth.permissions import MemberRole
from app.modules.auth.utils import create_access_token
from app.modules.billing.models import Plan
from app.modules.tenancy.models import Membership, Organization
from tests._database import DatabaseHandle

# T-291: this file needs billing enabled -- its routes, its tables, or
# both. CI's module-matrix job deselects it in the combination that turns
# every optional module off.
pytestmark = pytest.mark.module_dependent

# Plan is along for the ride: Organization.plan_id declares a real FK to
# plan.id (see test_usage_repository.py's identical note).
_TABLES = [User.__table__, Plan.__table__, Organization.__table__, Membership.__table__]


@pytest.fixture(scope="module")
def redis_container() -> "RedisContainer":
    with RedisContainer("redis:7-alpine") as container:
        yield container


# Function-scoped (not module-scoped): see test_billing_router.py -- an
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
    # Same swap-the-singleton's-storage approach as test_billing_router.py.
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
    organization = Organization(
        name="Acme", slug=f"acme-{uuid4().hex[:8]}", stripe_customer_id="cus_acme"
    )
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


def _headers(user: User, org: Organization) -> dict[str, str]:
    token = create_access_token(user.id, None, ())
    return {"Authorization": f"Bearer {token}", "X-Org-ID": str(org.id)}


class _FakeInvoiceList:
    def __init__(self, data: list[SimpleNamespace], *, has_more: bool) -> None:
        self.data = data
        self.has_more = has_more


def _invoice(invoice_id: str) -> SimpleNamespace:
    # SimpleNamespace, not a dict: InvoiceService reads Invoice objects via
    # attribute access (stripe.Invoice's typed fields), not dict subscripting.
    return SimpleNamespace(
        id=invoice_id,
        number="INV-001",
        amount_paid=2900,
        currency="usd",
        status="paid",
        hosted_invoice_url=f"https://invoice.stripe.com/{invoice_id}",
        invoice_pdf=f"https://invoice.stripe.com/{invoice_id}.pdf",
        period_start=1735689600,
        period_end=1738368000,
        created=1735689600,
    )


# --- GET /billing/invoices ----------------------------------------------------------


async def test_list_invoices_requires_auth(client: httpx.AsyncClient, org: Organization) -> None:
    response = await client.get("/api/v1/billing/invoices", headers={"X-Org-ID": str(org.id)})

    assert response.status_code == 401


async def test_list_invoices_requires_org_header(client: httpx.AsyncClient, owner: User) -> None:
    token = create_access_token(owner.id, None, ())

    response = await client.get(
        "/api/v1/billing/invoices", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 400


async def test_list_invoices_member_without_billing_read_returns_403(
    client: httpx.AsyncClient, session: AsyncSession, org: Organization
) -> None:
    member = await _add_member(session, org)

    response = await client.get("/api/v1/billing/invoices", headers=_headers(member, org))

    assert response.status_code == 403


async def test_list_invoices_admin_can_read(
    client: httpx.AsyncClient,
    session: AsyncSession,
    org: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await _add_member(session, org, role=MemberRole.ADMIN)

    async def _list(**_kwargs: object) -> _FakeInvoiceList:
        return _FakeInvoiceList([_invoice("in_1")], has_more=False)

    monkeypatch.setattr(stripe.Invoice, "list_async", _list)

    response = await client.get("/api/v1/billing/invoices", headers=_headers(admin, org))

    assert response.status_code == 200
    body = response.json()
    assert body["data"][0]["id"] == "in_1"
    assert body["data"][0]["amount_paid"] == 2900
    assert body["meta"]["next_cursor"] is None


async def test_list_invoices_returns_next_cursor_when_stripe_has_more(
    client: httpx.AsyncClient, owner: User, org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _list(**_kwargs: object) -> _FakeInvoiceList:
        return _FakeInvoiceList([_invoice("in_1"), _invoice("in_2")], has_more=True)

    monkeypatch.setattr(stripe.Invoice, "list_async", _list)

    response = await client.get("/api/v1/billing/invoices", headers=_headers(owner, org))

    assert response.status_code == 200
    assert response.json()["meta"]["next_cursor"] == "in_2"


async def test_list_invoices_resolves_customer_from_x_org_id_header_org(
    client: httpx.AsyncClient,
    session: AsyncSession,
    org: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other_owner = await _add_member(session, org, role=MemberRole.OWNER)
    other_org = Organization(
        name="Globex", slug=f"globex-{uuid4().hex[:8]}", stripe_customer_id="cus_globex"
    )
    session.add(other_org)
    await session.flush()
    session.add(Membership(user_id=other_owner.id, org_id=other_org.id, role=MemberRole.OWNER))
    await session.commit()

    captured: dict[str, Any] = {}

    async def _list(**kwargs: object) -> _FakeInvoiceList:
        captured.update(kwargs)
        return _FakeInvoiceList([], has_more=False)

    monkeypatch.setattr(stripe.Invoice, "list_async", _list)

    response = await client.get(
        "/api/v1/billing/invoices", headers=_headers(other_owner, other_org)
    )

    assert response.status_code == 200
    assert captured["customer"] == "cus_globex"


async def test_list_invoices_org_with_no_stripe_customer_returns_empty(
    client: httpx.AsyncClient, session: AsyncSession, owner: User
) -> None:
    org_without_billing = Organization(name="NoBilling", slug=f"nobilling-{uuid4().hex[:8]}")
    session.add(org_without_billing)
    await session.flush()
    session.add(Membership(user_id=owner.id, org_id=org_without_billing.id, role=MemberRole.OWNER))
    await session.commit()

    response = await client.get(
        "/api/v1/billing/invoices", headers=_headers(owner, org_without_billing)
    )

    assert response.status_code == 200
    assert response.json()["data"] == []
