"""
Verifies T-132's report_usage_to_stripe job (app.workers.jobs.usage):
unreported UsageRecords are grouped by (org_id, metric) and summed into a
single Stripe billing Meter event per group, records are marked reported
with that event's identifier afterward, and an org/metric that can't be
resolved (no Stripe customer, no subscription, or a plan that doesn't
configure the metric) is left unreported rather than erroring the whole
sweep. Same real-Postgres testcontainers + monkeypatched
`async_session_factory` pattern as test_trial_notifications_job.py --
`stripe.billing.MeterEvent.create` is monkeypatched instead of a real Stripe
call, same as that file's `stripe.Customer.retrieve` / `stripe.Invoice.retrieve`.
"""

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
import stripe
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

from app.modules.billing.models import Plan, Subscription
from app.modules.billing.repository import SubscriptionRepository, UsageRecordRepository
from app.modules.billing.usage_models import UsageRecord
from app.modules.tenancy.models import Organization
from app.workers.jobs import usage as usage_job_module
from app.workers.jobs.usage import report_usage_to_stripe
from tests._database import DatabaseHandle

_TABLES = [
    Organization.__table__,
    Plan.__table__,
    Subscription.__table__,
    UsageRecord.__table__,
]

_NOW = datetime(2026, 6, 15, 9, 0, tzinfo=UTC)


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


@pytest.fixture(autouse=True)
def _use_test_engine(monkeypatch: pytest.MonkeyPatch, test_engine: AsyncEngine) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(usage_job_module, "async_session_factory", factory)


@pytest.fixture
def session_factory(test_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as s:
        yield s


@dataclass
class _FakeMeterEvent:
    identifier: str


@dataclass
class _FakeMeterEventCreate:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, **params: Any) -> _FakeMeterEvent:  # noqa: ANN401 -- mirrors stripe's **params
        self.calls.append(params)
        return _FakeMeterEvent(identifier=str(params["identifier"]))


@pytest.fixture
def fake_meter_event_create(monkeypatch: pytest.MonkeyPatch) -> _FakeMeterEventCreate:
    fake = _FakeMeterEventCreate()
    monkeypatch.setattr(stripe.billing.MeterEvent, "create", fake)
    return fake


async def _make_org(
    session: AsyncSession, *, stripe_customer_id: str | None = "cus_1"
) -> Organization:
    org = Organization(
        name="Acme", slug=f"acme-{uuid4().hex[:8]}", stripe_customer_id=stripe_customer_id
    )
    session.add(org)
    await session.flush()
    return org


async def _make_plan(
    session: AsyncSession, *, usage_meter_event_names: dict[str, str] | None = None
) -> Plan:
    plan = Plan(
        name="Pro",
        slug=f"pro-{uuid4().hex[:8]}",
        stripe_price_id_monthly="price_m",
        stripe_price_id_yearly="price_y",
        price_monthly=2900,
        price_yearly=29000,
        features={"seats": 5},
        usage_meter_event_names=usage_meter_event_names or {},
    )
    session.add(plan)
    await session.flush()
    return plan


async def _make_subscription(session: AsyncSession, org: Organization, plan: Plan) -> Subscription:
    subs = SubscriptionRepository(session)
    subscription = await subs.upsert(
        org_id=org.id,
        plan_id=plan.id,
        stripe_subscription_id=f"sub_{uuid4().hex[:8]}",
        status="active",
        current_period_start=_NOW,
        current_period_end=_NOW,
        cancel_at_period_end=False,
        trial_end=None,
    )
    await session.commit()
    return subscription


async def test_unreported_records_are_summed_and_reported(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    fake_meter_event_create: _FakeMeterEventCreate,
) -> None:
    org = await _make_org(session, stripe_customer_id="cus_abc")
    plan = await _make_plan(session, usage_meter_event_names={"api_calls": "api_calls_meter"})
    await _make_subscription(session, org, plan)
    usage = UsageRecordRepository(session)
    first = await usage.create(org_id=org.id, metric="api_calls", quantity=3)
    second = await usage.create(org_id=org.id, metric="api_calls", quantity=4)
    await session.commit()

    await report_usage_to_stripe({})

    assert len(fake_meter_event_create.calls) == 1
    call = fake_meter_event_create.calls[0]
    assert call["event_name"] == "api_calls_meter"
    assert call["payload"] == {"stripe_customer_id": "cus_abc", "value": "7"}

    async with session_factory() as verify_session:
        reloaded_first = await verify_session.get(UsageRecord, first.id)
        reloaded_second = await verify_session.get(UsageRecord, second.id)
        assert reloaded_first is not None
        assert reloaded_second is not None
        assert reloaded_first.reported_to_stripe_at is not None
        assert reloaded_second.reported_to_stripe_at is not None
        assert reloaded_first.stripe_usage_record_id == reloaded_second.stripe_usage_record_id


async def test_groups_are_kept_separate_by_org_and_metric(
    session: AsyncSession,
    fake_meter_event_create: _FakeMeterEventCreate,
) -> None:
    plan = await _make_plan(
        session,
        usage_meter_event_names={"api_calls": "api_calls_meter", "ai_tokens": "ai_tokens_meter"},
    )
    org_a = await _make_org(session, stripe_customer_id="cus_a")
    org_b = await _make_org(session, stripe_customer_id="cus_b")
    await _make_subscription(session, org_a, plan)
    await _make_subscription(session, org_b, plan)
    usage = UsageRecordRepository(session)
    await usage.create(org_id=org_a.id, metric="api_calls", quantity=1)
    await usage.create(org_id=org_a.id, metric="ai_tokens", quantity=2)
    await usage.create(org_id=org_b.id, metric="api_calls", quantity=3)
    await session.commit()

    await report_usage_to_stripe({})

    assert len(fake_meter_event_create.calls) == 3


async def test_org_without_subscription_is_left_unreported(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    fake_meter_event_create: _FakeMeterEventCreate,
) -> None:
    org = await _make_org(session)
    usage = UsageRecordRepository(session)
    record = await usage.create(org_id=org.id, metric="api_calls", quantity=1)
    await session.commit()

    await report_usage_to_stripe({})

    assert fake_meter_event_create.calls == []
    async with session_factory() as verify_session:
        reloaded = await verify_session.get(UsageRecord, record.id)
        assert reloaded is not None
        assert reloaded.reported_to_stripe_at is None


async def test_org_without_stripe_customer_is_left_unreported(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    fake_meter_event_create: _FakeMeterEventCreate,
) -> None:
    org = await _make_org(session, stripe_customer_id=None)
    plan = await _make_plan(session, usage_meter_event_names={"api_calls": "api_calls_meter"})
    await _make_subscription(session, org, plan)
    usage = UsageRecordRepository(session)
    record = await usage.create(org_id=org.id, metric="api_calls", quantity=1)
    await session.commit()

    await report_usage_to_stripe({})

    assert fake_meter_event_create.calls == []
    async with session_factory() as verify_session:
        reloaded = await verify_session.get(UsageRecord, record.id)
        assert reloaded is not None
        assert reloaded.reported_to_stripe_at is None


async def test_metric_not_configured_on_plan_is_left_unreported(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    fake_meter_event_create: _FakeMeterEventCreate,
) -> None:
    org = await _make_org(session, stripe_customer_id="cus_1")
    plan = await _make_plan(session, usage_meter_event_names={})
    await _make_subscription(session, org, plan)
    usage = UsageRecordRepository(session)
    record = await usage.create(org_id=org.id, metric="api_calls", quantity=1)
    await session.commit()

    await report_usage_to_stripe({})

    assert fake_meter_event_create.calls == []
    async with session_factory() as verify_session:
        reloaded = await verify_session.get(UsageRecord, record.id)
        assert reloaded is not None
        assert reloaded.reported_to_stripe_at is None


async def test_already_reported_records_are_not_reported_again(
    session: AsyncSession,
    fake_meter_event_create: _FakeMeterEventCreate,
) -> None:
    org = await _make_org(session, stripe_customer_id="cus_1")
    plan = await _make_plan(session, usage_meter_event_names={"api_calls": "api_calls_meter"})
    await _make_subscription(session, org, plan)
    usage = UsageRecordRepository(session)
    await usage.create(org_id=org.id, metric="api_calls", quantity=1)
    await session.commit()

    await report_usage_to_stripe({})
    await report_usage_to_stripe({})

    assert len(fake_meter_event_create.calls) == 1
