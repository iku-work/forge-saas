"""
Verifies T-131's notify_expiring_trials and expire_trials_to_inactive jobs
(app.workers.jobs.billing): the 3-day/1-day warning windows are exact (a
trial_end just outside a window sends nothing, one just inside sends exactly
one email per OWNER), each warning is sent at most once per subscription via
the trial.warning_sent_3d / trial.warning_sent_1d audit markers, and
expire_trials_to_inactive only cancels + emails a trial that has actually
ended and has no Stripe payment method on file -- a trial with a card
attached is left for Stripe's own billing attempt (and T-030's webhook) to
resolve.

Same real-Postgres testcontainers + monkeypatched `async_session_factory`
pattern as test_dunning_job.py -- the jobs open their own session rather than
taking one as a parameter, and `send_email` calls go through
tests/conftest.py's `enqueued_jobs` fixture instead of a real Redis.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
import stripe
from freezegun import freeze_time
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel
from testcontainers.postgres import PostgresContainer

from app.modules.audit.models import AuditEvent
from app.modules.auth.models import User
from app.modules.auth.permissions import MemberRole
from app.modules.billing.models import Plan, Subscription
from app.modules.billing.repository import SubscriptionRepository
from app.modules.tenancy.models import Membership, Organization
from app.workers.jobs import billing as billing_job_module
from app.workers.jobs.billing import expire_trials_to_inactive, notify_expiring_trials

_TABLES = [
    User.__table__,
    Organization.__table__,
    Membership.__table__,
    Plan.__table__,
    Subscription.__table__,
    AuditEvent.__table__,
]

_NOW = datetime(2026, 6, 15, 9, 0, tzinfo=UTC)


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


@pytest.fixture(autouse=True)
def _use_test_engine(monkeypatch: pytest.MonkeyPatch, test_engine: AsyncEngine) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(billing_job_module, "async_session_factory", factory)


@pytest.fixture
def session_factory(test_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as s:
        yield s


class _FakeInvoiceSettings:
    def __init__(self, default_payment_method: str | None) -> None:
        self.default_payment_method = default_payment_method


class _FakeCustomer:
    def __init__(
        self, *, default_source: str | None = None, default_payment_method: str | None = None
    ) -> None:
        self.default_source = default_source
        self.invoice_settings = _FakeInvoiceSettings(default_payment_method)
        self.deleted = False


@pytest.fixture
def fake_stripe_customer(monkeypatch: pytest.MonkeyPatch) -> dict[str, _FakeCustomer]:
    customers: dict[str, _FakeCustomer] = {}
    monkeypatch.setattr(stripe.Customer, "retrieve", lambda customer_id: customers[customer_id])
    return customers


async def _make_org_with_owners(
    session: AsyncSession, *, owners: int = 1, stripe_customer_id: str | None = None
) -> Organization:
    org = Organization(
        name="Acme", slug=f"acme-{uuid4().hex[:8]}", stripe_customer_id=stripe_customer_id
    )
    session.add(org)
    await session.flush()

    for i in range(owners):
        user = User(email=f"owner{i}-{uuid4().hex[:6]}@example.com", full_name=f"Owner {i}")
        session.add(user)
        await session.flush()
        session.add(Membership(user_id=user.id, org_id=org.id, role=MemberRole.OWNER))

    await session.flush()
    return org


async def _make_plan(session: AsyncSession) -> Plan:
    plan = Plan(
        name="Pro",
        slug=f"pro-{uuid4().hex[:8]}",
        stripe_price_id_monthly="price_m",
        stripe_price_id_yearly="price_y",
        price_monthly=2900,
        price_yearly=29000,
        features={"seats": 5},
    )
    session.add(plan)
    await session.flush()
    return plan


async def _make_trialing_subscription(
    session: AsyncSession, org: Organization, plan: Plan, *, trial_end: datetime
) -> Subscription:
    subs = SubscriptionRepository(session)
    subscription = await subs.upsert(
        org_id=org.id,
        plan_id=plan.id,
        stripe_subscription_id=f"sub_{uuid4().hex[:8]}",
        status="trialing",
        current_period_start=_NOW - timedelta(days=1),
        current_period_end=trial_end,
        cancel_at_period_end=False,
        trial_end=trial_end,
    )
    await session.commit()
    return subscription


# --- notify_expiring_trials -------------------------------------------------


async def test_trial_end_just_outside_3d_window_sends_nothing(
    session: AsyncSession, enqueued_jobs: list[tuple[str, dict[str, Any]]]
) -> None:
    org = await _make_org_with_owners(session)
    plan = await _make_plan(session)
    await _make_trialing_subscription(
        session, org, plan, trial_end=_NOW + timedelta(days=3, hours=2)
    )

    with freeze_time(_NOW):
        await notify_expiring_trials({})

    assert enqueued_jobs == []


async def test_trial_end_inside_3d_window_sends_3d_warning(
    session: AsyncSession, enqueued_jobs: list[tuple[str, dict[str, Any]]]
) -> None:
    org = await _make_org_with_owners(session, owners=2)
    plan = await _make_plan(session)
    trial_end = _NOW + timedelta(days=3, minutes=30)
    await _make_trialing_subscription(session, org, plan, trial_end=trial_end)

    with freeze_time(_NOW):
        await notify_expiring_trials({})

    assert len(enqueued_jobs) == 2
    for function, kwargs in enqueued_jobs:
        assert function == "send_email"
        assert kwargs["template"] == "trial_ending"
        assert kwargs["context"]["org_name"] == "Acme"
        assert kwargs["context"]["trial_end_date"] == trial_end.date().isoformat()


async def test_trial_end_inside_1d_window_sends_1d_warning(
    session: AsyncSession, enqueued_jobs: list[tuple[str, dict[str, Any]]]
) -> None:
    org = await _make_org_with_owners(session)
    plan = await _make_plan(session)
    await _make_trialing_subscription(
        session, org, plan, trial_end=_NOW + timedelta(days=1, minutes=30)
    )

    with freeze_time(_NOW):
        await notify_expiring_trials({})

    assert len(enqueued_jobs) == 1
    _function, kwargs = enqueued_jobs[0]
    assert kwargs["template"] == "trial_ending"


async def test_rerunning_same_horizon_does_not_resend(
    session: AsyncSession, enqueued_jobs: list[tuple[str, dict[str, Any]]]
) -> None:
    org = await _make_org_with_owners(session)
    plan = await _make_plan(session)
    await _make_trialing_subscription(
        session, org, plan, trial_end=_NOW + timedelta(days=3, minutes=30)
    )

    with freeze_time(_NOW):
        await notify_expiring_trials({})
        await notify_expiring_trials({})

    assert len(enqueued_jobs) == 1


# --- expire_trials_to_inactive -----------------------------------------------


async def test_expired_trial_without_payment_method_is_canceled_and_emailed(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    org = await _make_org_with_owners(session, stripe_customer_id=None)
    plan = await _make_plan(session)
    subscription = await _make_trialing_subscription(
        session, org, plan, trial_end=_NOW - timedelta(hours=1)
    )

    with freeze_time(_NOW):
        await expire_trials_to_inactive({})

    assert len(enqueued_jobs) == 1
    _function, kwargs = enqueued_jobs[0]
    assert kwargs["template"] == "trial_expired"
    assert kwargs["context"]["org_name"] == "Acme"

    async with session_factory() as verify_session:
        reloaded = await verify_session.get(Subscription, subscription.id)
        assert reloaded is not None
        assert reloaded.status == "canceled"


async def test_expired_trial_with_payment_method_is_left_alone(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
    fake_stripe_customer: dict[str, _FakeCustomer],
) -> None:
    fake_stripe_customer["cus_with_card"] = _FakeCustomer(default_payment_method="pm_123")
    org = await _make_org_with_owners(session, stripe_customer_id="cus_with_card")
    plan = await _make_plan(session)
    subscription = await _make_trialing_subscription(
        session, org, plan, trial_end=_NOW - timedelta(hours=1)
    )

    with freeze_time(_NOW):
        await expire_trials_to_inactive({})

    assert enqueued_jobs == []
    async with session_factory() as verify_session:
        reloaded = await verify_session.get(Subscription, subscription.id)
        assert reloaded is not None
        assert reloaded.status == "trialing"


async def test_trial_not_yet_expired_is_left_alone(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    org = await _make_org_with_owners(session, stripe_customer_id=None)
    plan = await _make_plan(session)
    subscription = await _make_trialing_subscription(
        session, org, plan, trial_end=_NOW + timedelta(hours=1)
    )

    with freeze_time(_NOW):
        await expire_trials_to_inactive({})

    assert enqueued_jobs == []
    async with session_factory() as verify_session:
        reloaded = await verify_session.get(Subscription, subscription.id)
        assert reloaded is not None
        assert reloaded.status == "trialing"


async def test_rerunning_expire_does_not_resend_the_email(
    session: AsyncSession, enqueued_jobs: list[tuple[str, dict[str, Any]]]
) -> None:
    org = await _make_org_with_owners(session, stripe_customer_id=None)
    plan = await _make_plan(session)
    await _make_trialing_subscription(session, org, plan, trial_end=_NOW - timedelta(hours=1))

    with freeze_time(_NOW):
        await expire_trials_to_inactive({})
        # Second run: the subscription is already "canceled" so the query
        # in list_trialing_expired no longer returns it -- confirms the
        # status transition itself is what makes the job's next tick a
        # no-op, not just the audit marker.
        await expire_trials_to_inactive({})

    assert len(enqueued_jobs) == 1
