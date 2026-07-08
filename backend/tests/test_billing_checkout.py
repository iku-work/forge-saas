"""
Verifies T-031's checkout/portal/customer-lifecycle additions to
BillingService: get_or_create_stripe_customer is idempotent (a second call
returns the cached id without touching Stripe again), create_checkout_session
resolves the right Stripe price for the requested billing interval and
stamps org_id onto both the Checkout Session and -- critically --
subscription_data.metadata, since _handle_subscription_upserted (T-030)
reads org_id off the *Subscription* object's metadata, not the Checkout
Session's, and create_portal_session reuses the same customer resolution.

stripe.Customer.create_async / stripe.checkout.Session.create_async /
stripe.billing_portal.Session.create_async are monkeypatched rather than
hitting Stripe's network -- these tests verify the *request* this service
builds, not Stripe's response handling (that's test_billing_webhooks.py's
job). T-206 switched these call sites onto stripe's `_async` method variants
(so they can be wrapped in app.core.resilience's async `@resilient("stripe")`
without blocking the event loop), so the monkeypatched replacements below are
themselves `async def`. Same real-Postgres testcontainers pattern as
test_billing_service.py.
"""

from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

import pytest
import stripe
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel
from testcontainers.postgres import PostgresContainer

from app.core.exceptions import DomainError, NotFoundError
from app.modules.billing.models import Plan, Subscription
from app.modules.billing.service import BillingService
from app.modules.tenancy.models import Organization

_TABLES = [Plan.__table__, Subscription.__table__, Organization.__table__]


@pytest.fixture(scope="module")
def postgres_container() -> "PostgresContainer":
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


# Function-scoped (not module-scoped): see test_billing_service.py -- an
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
def redis(_redis_client: Redis) -> Redis:
    return _redis_client


@pytest.fixture
def service(session: AsyncSession, redis: Redis) -> BillingService:
    return BillingService(session, redis)


@pytest.fixture
async def org(session: AsyncSession) -> Organization:
    organization = Organization(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    session.add(organization)
    await session.flush()
    return organization


async def _make_plan(session: AsyncSession) -> Plan:
    plan = Plan(
        name="Pro",
        slug=f"pro-{uuid4().hex[:8]}",
        stripe_price_id_monthly="price_monthly_1",
        stripe_price_id_yearly="price_yearly_1",
        price_monthly=2900,
        price_yearly=29000,
        features={"seats": 5},
    )
    session.add(plan)
    await session.flush()
    return plan


class _FakeCustomer:
    def __init__(self, customer_id: str) -> None:
        self.id = customer_id


class _FakeCheckoutSession:
    def __init__(self, url: str | None) -> None:
        self.url = url


class _FakePortalSession:
    def __init__(self, url: str) -> None:
        self.url = url


# --- get_or_create_stripe_customer ----------------------------------------------------


async def test_creates_customer_when_org_has_none(
    service: BillingService, org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_count = 0

    async def _create(**_kwargs: object) -> _FakeCustomer:
        nonlocal call_count
        call_count += 1
        return _FakeCustomer(customer_id="cus_new")

    monkeypatch.setattr(stripe.Customer, "create_async", _create)

    customer_id = await service.get_or_create_stripe_customer(org)

    assert customer_id == "cus_new"
    assert org.stripe_customer_id == "cus_new"
    assert call_count == 1


async def test_reuses_existing_customer_without_calling_stripe(
    service: BillingService, org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    org.stripe_customer_id = "cus_existing"

    async def _create(**_kwargs: object) -> _FakeCustomer:
        raise AssertionError("stripe.Customer.create_async should not be called")

    monkeypatch.setattr(stripe.Customer, "create_async", _create)

    customer_id = await service.get_or_create_stripe_customer(org)

    assert customer_id == "cus_existing"


# --- create_checkout_session -----------------------------------------------------------


async def test_checkout_session_unknown_plan_raises_not_found(
    service: BillingService, org: Organization
) -> None:
    with pytest.raises(NotFoundError):
        await service.create_checkout_session(org, "does-not-exist", "monthly")


async def test_checkout_session_builds_request_with_org_metadata(
    service: BillingService,
    session: AsyncSession,
    org: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = await _make_plan(session)
    captured: dict[str, Any] = {}

    async def _customer_create(**_kwargs: object) -> _FakeCustomer:
        return _FakeCustomer("cus_1")

    monkeypatch.setattr(stripe.Customer, "create_async", _customer_create)

    async def _checkout_create(**kwargs: object) -> _FakeCheckoutSession:
        captured.update(kwargs)
        return _FakeCheckoutSession(url="https://checkout.stripe.com/test")

    monkeypatch.setattr(stripe.checkout.Session, "create_async", _checkout_create)

    url = await service.create_checkout_session(org, plan.slug, "monthly")

    assert url == "https://checkout.stripe.com/test"
    assert captured["customer"] == "cus_1"
    assert captured["mode"] == "subscription"
    assert captured["line_items"] == [{"price": "price_monthly_1", "quantity": 1}]
    assert captured["metadata"] == {"org_id": str(org.id)}
    assert captured["subscription_data"] == {"metadata": {"org_id": str(org.id)}}


async def test_checkout_session_yearly_interval_uses_yearly_price(
    service: BillingService,
    session: AsyncSession,
    org: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = await _make_plan(session)
    captured: dict[str, Any] = {}

    async def _customer_create(**_kwargs: object) -> _FakeCustomer:
        return _FakeCustomer("cus_1")

    monkeypatch.setattr(stripe.Customer, "create_async", _customer_create)

    async def _checkout_create(**kwargs: object) -> _FakeCheckoutSession:
        captured.update(kwargs)
        return _FakeCheckoutSession(url="https://checkout.stripe.com/test")

    monkeypatch.setattr(stripe.checkout.Session, "create_async", _checkout_create)

    await service.create_checkout_session(org, plan.slug, "yearly")

    assert captured["line_items"] == [{"price": "price_yearly_1", "quantity": 1}]


async def test_checkout_session_no_url_raises_domain_error(
    service: BillingService,
    session: AsyncSession,
    org: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = await _make_plan(session)

    async def _customer_create(**_kwargs: object) -> _FakeCustomer:
        return _FakeCustomer("cus_1")

    async def _checkout_create(**_kwargs: object) -> _FakeCheckoutSession:
        return _FakeCheckoutSession(url=None)

    monkeypatch.setattr(stripe.Customer, "create_async", _customer_create)
    monkeypatch.setattr(stripe.checkout.Session, "create_async", _checkout_create)

    with pytest.raises(DomainError):
        await service.create_checkout_session(org, plan.slug, "monthly")


# --- create_portal_session --------------------------------------------------------------


async def test_portal_session_builds_request_with_customer(
    service: BillingService, org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    org.stripe_customer_id = "cus_existing"
    captured: dict[str, Any] = {}

    async def _portal_create(**kwargs: object) -> _FakePortalSession:
        captured.update(kwargs)
        return _FakePortalSession(url="https://billing.stripe.com/portal/test")

    monkeypatch.setattr(stripe.billing_portal.Session, "create_async", _portal_create)

    url = await service.create_portal_session(org)

    assert url == "https://billing.stripe.com/portal/test"
    assert captured["customer"] == "cus_existing"


# --- get_subscription / list_active_plans --------------------------------------------


async def test_get_subscription_returns_none_when_absent(
    service: BillingService, org: Organization
) -> None:
    assert await service.get_subscription(org.id) is None


async def test_list_active_plans_excludes_inactive(
    service: BillingService, session: AsyncSession
) -> None:
    active = await _make_plan(session)
    inactive = await _make_plan(session)
    inactive.is_active = False
    await session.flush()

    plans = await service.list_active_plans()

    ids = {p.id for p in plans}
    assert active.id in ids
    assert inactive.id not in ids
