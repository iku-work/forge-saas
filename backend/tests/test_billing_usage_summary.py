"""
Verifies BillingService.get_usage_summary(): combines the org's current
subscription period usage (UsageService.aggregate) with the plan's local
Plan.usage_prices_micros to estimate a per-metric cost -- see
docs/runbooks/stripe-usage-pricing.md. Same real-Postgres testcontainers
pattern as test_billing_service.py/test_usage_service.py.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel
from testcontainers.postgres import PostgresContainer

from app.modules.billing.models import Plan, Subscription
from app.modules.billing.service import BillingService
from app.modules.billing.usage_models import UsageRecord
from app.modules.billing.usage_service import UsageService
from app.modules.tenancy.models import Organization
from app.modules.tenancy.repository import OrganizationRepository

_TABLES = [Plan.__table__, Organization.__table__, Subscription.__table__, UsageRecord.__table__]


@pytest.fixture(scope="module")
def postgres_container() -> "PostgresContainer":
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


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


async def _make_org(session: AsyncSession) -> Organization:
    return await OrganizationRepository(session).create(name="Acme", slug=f"acme-{uuid4().hex[:8]}")


async def _make_plan(
    session: AsyncSession,
    *,
    usage_meter_event_names: dict[str, str] | None = None,
    usage_prices_micros: dict[str, int] | None = None,
) -> Plan:
    plan = Plan(
        name="Pro",
        slug=f"pro-{uuid4().hex[:8]}",
        stripe_price_id_monthly="price_pro_monthly",
        stripe_price_id_yearly="price_pro_yearly",
        price_monthly=2900,
        price_yearly=29000,
        features={"seats": 5},
        usage_meter_event_names=usage_meter_event_names or {},
        usage_prices_micros=usage_prices_micros or {},
    )
    session.add(plan)
    await session.flush()
    return plan


async def _make_subscription(
    session: AsyncSession,
    *,
    org_id: UUID,
    plan_id: UUID,
    period_start: datetime,
    period_end: datetime,
) -> Subscription:
    subscription = Subscription(
        org_id=org_id,
        plan_id=plan_id,
        stripe_subscription_id=f"sub_{uuid4().hex[:8]}",
        status="active",
        current_period_start=period_start,
        current_period_end=period_end,
    )
    session.add(subscription)
    await session.flush()
    return subscription


async def test_no_subscription_returns_empty_list(service: BillingService) -> None:
    assert await service.get_usage_summary(uuid4()) == []


async def test_metric_with_price_computes_estimated_cost(
    session: AsyncSession, service: BillingService
) -> None:
    org = await _make_org(session)
    plan = await _make_plan(
        session,
        usage_meter_event_names={"api_calls": "api_calls_meter"},
        usage_prices_micros={"api_calls": 100},  # $0.0001/call
    )
    now = datetime.now(UTC)
    await _make_subscription(
        session,
        org_id=org.id,
        plan_id=plan.id,
        period_start=now - timedelta(days=1),
        period_end=now + timedelta(days=29),
    )
    usage = UsageService(session, org.id)
    await usage.record(metric="api_calls", quantity=250)
    await usage.record(metric="api_calls", quantity=50)

    summary = await service.get_usage_summary(org.id)

    assert [item.model_dump() for item in summary] == [
        {
            "metric": "api_calls",
            "quantity": 300,
            "unit_price_micros": 100,
            "estimated_cost_micros": 30_000,
        }
    ]


async def test_metric_without_price_returns_none_price_and_cost(
    session: AsyncSession, service: BillingService
) -> None:
    org = await _make_org(session)
    plan = await _make_plan(
        session,
        usage_meter_event_names={"exports": "exports_meter"},
        usage_prices_micros={},  # tracked, not priced -- e.g. an included quota metric
    )
    now = datetime.now(UTC)
    await _make_subscription(
        session,
        org_id=org.id,
        plan_id=plan.id,
        period_start=now - timedelta(days=1),
        period_end=now + timedelta(days=29),
    )
    await UsageService(session, org.id).record(metric="exports", quantity=3)

    summary = await service.get_usage_summary(org.id)

    assert [item.model_dump() for item in summary] == [
        {
            "metric": "exports",
            "quantity": 3,
            "unit_price_micros": None,
            "estimated_cost_micros": None,
        }
    ]


async def test_usage_outside_current_period_excluded(
    session: AsyncSession, service: BillingService
) -> None:
    org = await _make_org(session)
    plan = await _make_plan(
        session,
        usage_meter_event_names={"api_calls": "api_calls_meter"},
        usage_prices_micros={"api_calls": 100},
    )
    now = datetime.now(UTC)
    await _make_subscription(
        session,
        org_id=org.id,
        plan_id=plan.id,
        period_start=now - timedelta(days=1),
        period_end=now + timedelta(days=29),
    )
    usage = UsageService(session, org.id)
    await usage.record(metric="api_calls", quantity=10)  # inside the window
    # Directly inserted, not via record(), so occurred_at can be backdated
    # before the window -- record() always stamps "now" via its own default.
    session.add(
        UsageRecord(
            org_id=org.id,
            metric="api_calls",
            quantity=999,
            occurred_at=now - timedelta(days=10),
        )
    )
    await session.flush()

    summary = await service.get_usage_summary(org.id)

    assert summary[0].quantity == 10
    assert summary[0].estimated_cost_micros == 1_000
