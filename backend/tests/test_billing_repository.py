"""
Verifies T-030's billing repositories: PlanRepository resolves a Stripe price
id from either the monthly or yearly column, SubscriptionRepository's
upsert() both creates and updates by stripe_subscription_id (never by org_id,
since a webhook redelivery must hit the same row it created the first time),
and WebhookEventRepository is the idempotency ledger app.modules.billing.
webhooks relies on. Same real-Postgres testcontainers pattern as
test_audit_repository.py.

Also covers T-031's additions: PlanRepository.get_by_id/get_by_slug/
list_active (consulted by app.modules.billing.dependencies and .service) and
SubscriptionRepository.get_by_org_id (the one-subscription-per-org lookup
the plan-gating dependency resolves entitlement from).
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

from app.modules.billing.models import DunningCampaign, Plan, Subscription, WebhookEvent
from app.modules.billing.repository import (
    DunningCampaignRepository,
    PlanRepository,
    SubscriptionRepository,
    WebhookEventRepository,
)
from tests._database import DatabaseHandle

_TABLES = [
    Plan.__table__,
    Subscription.__table__,
    WebhookEvent.__table__,
    DunningCampaign.__table__,
]


# Function-scoped (not module-scoped): see test_audit_repository.py — an
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
async def session(test_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s


@pytest.fixture
def plans(session: AsyncSession) -> PlanRepository:
    return PlanRepository(session)


@pytest.fixture
def subscriptions(session: AsyncSession) -> SubscriptionRepository:
    return SubscriptionRepository(session)


@pytest.fixture
def webhook_events(session: AsyncSession) -> WebhookEventRepository:
    return WebhookEventRepository(session)


@pytest.fixture
def dunning_campaigns(session: AsyncSession) -> DunningCampaignRepository:
    return DunningCampaignRepository(session)


async def _make_subscription(session: AsyncSession, plan: Plan) -> Subscription:
    subscription = Subscription(
        org_id=uuid4(),
        plan_id=plan.id,
        stripe_subscription_id=f"sub_{uuid4().hex[:8]}",
        status="past_due",
        current_period_start=datetime(2026, 1, 1, tzinfo=UTC),
        current_period_end=datetime(2026, 2, 1, tzinfo=UTC),
    )
    session.add(subscription)
    await session.flush()
    return subscription


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


# --- PlanRepository ---------------------------------------------------------------


async def test_get_by_stripe_price_id_matches_monthly_price(
    plans: PlanRepository, session: AsyncSession
) -> None:
    plan = await _make_plan(session)

    found = await plans.get_by_stripe_price_id("price_monthly_1")

    assert found is not None
    assert found.id == plan.id


async def test_get_by_stripe_price_id_matches_yearly_price(
    plans: PlanRepository, session: AsyncSession
) -> None:
    plan = await _make_plan(session)

    found = await plans.get_by_stripe_price_id("price_yearly_1")

    assert found is not None
    assert found.id == plan.id


async def test_get_by_stripe_price_id_unknown_returns_none(plans: PlanRepository) -> None:
    assert await plans.get_by_stripe_price_id("price_unknown") is None


async def test_get_by_id_returns_plan(plans: PlanRepository, session: AsyncSession) -> None:
    plan = await _make_plan(session)

    found = await plans.get_by_id(plan.id)

    assert found is not None
    assert found.id == plan.id


async def test_plan_get_by_id_unknown_returns_none(plans: PlanRepository) -> None:
    assert await plans.get_by_id(uuid4()) is None


async def test_get_by_slug_returns_active_plan(
    plans: PlanRepository, session: AsyncSession
) -> None:
    plan = await _make_plan(session)

    found = await plans.get_by_slug(plan.slug)

    assert found is not None
    assert found.id == plan.id


async def test_get_by_slug_unknown_returns_none(plans: PlanRepository) -> None:
    assert await plans.get_by_slug("does-not-exist") is None


async def test_get_by_slug_inactive_plan_returns_none(
    plans: PlanRepository, session: AsyncSession
) -> None:
    plan = await _make_plan(session)
    plan.is_active = False
    await session.flush()

    assert await plans.get_by_slug(plan.slug) is None


async def test_list_active_excludes_inactive_and_soft_deleted_plans(
    plans: PlanRepository, session: AsyncSession
) -> None:
    active = await _make_plan(session)
    inactive = await _make_plan(session)
    inactive.is_active = False
    deleted = await _make_plan(session)
    deleted.deleted_at = datetime(2026, 1, 1, tzinfo=UTC)
    await session.flush()

    found = await plans.list_active()

    found_ids = {p.id for p in found}
    assert active.id in found_ids
    assert inactive.id not in found_ids
    assert deleted.id not in found_ids


# --- SubscriptionRepository --------------------------------------------------------


async def test_upsert_creates_new_subscription(
    subscriptions: SubscriptionRepository, session: AsyncSession
) -> None:
    plan = await _make_plan(session)
    org_id = uuid4()

    sub = await subscriptions.upsert(
        org_id=org_id,
        plan_id=plan.id,
        stripe_subscription_id="sub_1",
        status="active",
        current_period_start=datetime(2026, 1, 1, tzinfo=UTC),
        current_period_end=datetime(2026, 2, 1, tzinfo=UTC),
        cancel_at_period_end=False,
        trial_end=None,
    )

    assert sub.org_id == org_id
    assert sub.status == "active"


async def test_upsert_updates_existing_subscription_by_stripe_id(
    subscriptions: SubscriptionRepository, session: AsyncSession
) -> None:
    plan = await _make_plan(session)
    org_id = uuid4()
    await subscriptions.upsert(
        org_id=org_id,
        plan_id=plan.id,
        stripe_subscription_id="sub_2",
        status="trialing",
        current_period_start=datetime(2026, 1, 1, tzinfo=UTC),
        current_period_end=datetime(2026, 2, 1, tzinfo=UTC),
        cancel_at_period_end=False,
        trial_end=datetime(2026, 1, 15, tzinfo=UTC),
    )

    updated = await subscriptions.upsert(
        org_id=org_id,
        plan_id=plan.id,
        stripe_subscription_id="sub_2",
        status="active",
        current_period_start=datetime(2026, 2, 1, tzinfo=UTC),
        current_period_end=datetime(2026, 3, 1, tzinfo=UTC),
        cancel_at_period_end=True,
        trial_end=None,
    )

    assert updated.status == "active"
    assert updated.cancel_at_period_end is True
    assert updated.trial_end is None

    found = await subscriptions.get_by_stripe_subscription_id("sub_2")
    assert found is not None
    assert found.id == updated.id


async def test_get_by_stripe_subscription_id_unknown_returns_none(
    subscriptions: SubscriptionRepository,
) -> None:
    assert await subscriptions.get_by_stripe_subscription_id("sub_unknown") is None


async def test_subscription_get_by_id_returns_subscription(
    subscriptions: SubscriptionRepository, session: AsyncSession
) -> None:
    plan = await _make_plan(session)
    sub = await _make_subscription(session, plan)

    found = await subscriptions.get_by_id(sub.id)

    assert found is not None
    assert found.id == sub.id


async def test_subscription_get_by_id_unknown_returns_none(
    subscriptions: SubscriptionRepository,
) -> None:
    assert await subscriptions.get_by_id(uuid4()) is None


async def test_get_by_org_id_returns_subscription(
    subscriptions: SubscriptionRepository, session: AsyncSession
) -> None:
    plan = await _make_plan(session)
    org_id = uuid4()
    await subscriptions.upsert(
        org_id=org_id,
        plan_id=plan.id,
        stripe_subscription_id="sub_org_lookup",
        status="active",
        current_period_start=datetime(2026, 1, 1, tzinfo=UTC),
        current_period_end=datetime(2026, 2, 1, tzinfo=UTC),
        cancel_at_period_end=False,
        trial_end=None,
    )

    found = await subscriptions.get_by_org_id(org_id)

    assert found is not None
    assert found.org_id == org_id


async def test_get_by_org_id_unknown_returns_none(
    subscriptions: SubscriptionRepository,
) -> None:
    assert await subscriptions.get_by_org_id(uuid4()) is None


async def test_set_status_by_stripe_subscription_id_updates_status(
    subscriptions: SubscriptionRepository, session: AsyncSession
) -> None:
    plan = await _make_plan(session)
    await subscriptions.upsert(
        org_id=uuid4(),
        plan_id=plan.id,
        stripe_subscription_id="sub_3",
        status="active",
        current_period_start=datetime(2026, 1, 1, tzinfo=UTC),
        current_period_end=datetime(2026, 2, 1, tzinfo=UTC),
        cancel_at_period_end=False,
        trial_end=None,
    )

    updated = await subscriptions.set_status_by_stripe_subscription_id("sub_3", "past_due")

    assert updated is not None
    assert updated.status == "past_due"


async def test_set_status_by_stripe_subscription_id_unknown_returns_none(
    subscriptions: SubscriptionRepository,
) -> None:
    result = await subscriptions.set_status_by_stripe_subscription_id("sub_unknown", "canceled")

    assert result is None


async def test_count_trialing_expiring_within_counts_only_trialing_inside_horizon(
    subscriptions: SubscriptionRepository, session: AsyncSession
) -> None:
    plan = await _make_plan(session)
    horizon = datetime(2026, 1, 4, tzinfo=UTC)
    await subscriptions.upsert(
        org_id=uuid4(),
        plan_id=plan.id,
        stripe_subscription_id="sub_trial_inside",
        status="trialing",
        current_period_start=datetime(2026, 1, 1, tzinfo=UTC),
        current_period_end=datetime(2026, 2, 1, tzinfo=UTC),
        cancel_at_period_end=False,
        trial_end=datetime(2026, 1, 3, tzinfo=UTC),
    )
    # Outside the horizon -- must not be counted.
    await subscriptions.upsert(
        org_id=uuid4(),
        plan_id=plan.id,
        stripe_subscription_id="sub_trial_outside",
        status="trialing",
        current_period_start=datetime(2026, 1, 1, tzinfo=UTC),
        current_period_end=datetime(2026, 2, 1, tzinfo=UTC),
        cancel_at_period_end=False,
        trial_end=datetime(2026, 2, 1, tzinfo=UTC),
    )
    # Inside the horizon but not trialing -- must not be counted either.
    await subscriptions.upsert(
        org_id=uuid4(),
        plan_id=plan.id,
        stripe_subscription_id="sub_active",
        status="active",
        current_period_start=datetime(2026, 1, 1, tzinfo=UTC),
        current_period_end=datetime(2026, 2, 1, tzinfo=UTC),
        cancel_at_period_end=False,
        trial_end=datetime(2026, 1, 3, tzinfo=UTC),
    )

    count = await subscriptions.count_trialing_expiring_within(horizon)

    assert count == 1


async def test_count_trialing_expiring_within_ignores_null_trial_end(
    subscriptions: SubscriptionRepository, session: AsyncSession
) -> None:
    plan = await _make_plan(session)
    await subscriptions.upsert(
        org_id=uuid4(),
        plan_id=plan.id,
        stripe_subscription_id="sub_no_trial",
        status="trialing",
        current_period_start=datetime(2026, 1, 1, tzinfo=UTC),
        current_period_end=datetime(2026, 2, 1, tzinfo=UTC),
        cancel_at_period_end=False,
        trial_end=None,
    )

    count = await subscriptions.count_trialing_expiring_within(datetime(2026, 6, 1, tzinfo=UTC))

    assert count == 0


# --- WebhookEventRepository ---------------------------------------------------------


async def test_create_then_get_by_id_round_trips(webhook_events: WebhookEventRepository) -> None:
    created = await webhook_events.create(
        event_id="evt_1", event_type="customer.subscription.created", payload={"id": "evt_1"}
    )

    found = await webhook_events.get_by_id("evt_1")

    assert found is not None
    assert found.id == created.id
    assert found.type == "customer.subscription.created"
    assert found.processed_at is None
    assert found.attempt_count == 0


async def test_get_by_id_unknown_returns_none(webhook_events: WebhookEventRepository) -> None:
    assert await webhook_events.get_by_id("evt_unknown") is None


async def test_mark_processed_sets_processed_at(webhook_events: WebhookEventRepository) -> None:
    event = await webhook_events.create(
        event_id="evt_2", event_type="invoice.payment_succeeded", payload={}
    )

    await webhook_events.mark_processed(event)

    assert event.processed_at is not None


async def test_mark_failed_sets_error_and_increments_attempt_count(
    webhook_events: WebhookEventRepository,
) -> None:
    event = await webhook_events.create(
        event_id="evt_3", event_type="invoice.payment_failed", payload={}
    )

    await webhook_events.mark_failed(event, error="boom")
    await webhook_events.mark_failed(event, error="boom again")

    assert event.error == "boom again"
    assert event.attempt_count == 2
    assert event.processed_at is None


# --- DunningCampaignRepository (T-130) -----------------------------------------------


async def test_dunning_create_then_get_by_id_round_trips(
    dunning_campaigns: DunningCampaignRepository, session: AsyncSession
) -> None:
    plan = await _make_plan(session)
    sub = await _make_subscription(session, plan)

    created = await dunning_campaigns.create(
        subscription_id=sub.id, invoice_id="in_1", scheduled_job_ids=["job_1", "job_2", "job_3"]
    )

    found = await dunning_campaigns.get_by_id(created.id)

    assert found is not None
    assert found.subscription_id == sub.id
    assert found.invoice_id == "in_1"
    assert found.status == "active"
    assert found.scheduled_job_ids == ["job_1", "job_2", "job_3"]
    assert found.ended_at is None


async def test_dunning_get_by_id_unknown_returns_none(
    dunning_campaigns: DunningCampaignRepository,
) -> None:
    assert await dunning_campaigns.get_by_id(uuid4()) is None


async def test_dunning_get_active_by_subscription_id_returns_active_campaign(
    dunning_campaigns: DunningCampaignRepository, session: AsyncSession
) -> None:
    plan = await _make_plan(session)
    sub = await _make_subscription(session, plan)
    created = await dunning_campaigns.create(
        subscription_id=sub.id, invoice_id="in_1", scheduled_job_ids=[]
    )

    found = await dunning_campaigns.get_active_by_subscription_id(sub.id)

    assert found is not None
    assert found.id == created.id


async def test_dunning_get_active_by_subscription_id_excludes_closed_campaigns(
    dunning_campaigns: DunningCampaignRepository, session: AsyncSession
) -> None:
    plan = await _make_plan(session)
    sub = await _make_subscription(session, plan)
    created = await dunning_campaigns.create(
        subscription_id=sub.id, invoice_id="in_1", scheduled_job_ids=[]
    )
    await dunning_campaigns.close(created, status="completed")

    assert await dunning_campaigns.get_active_by_subscription_id(sub.id) is None


async def test_dunning_get_active_by_subscription_id_unknown_returns_none(
    dunning_campaigns: DunningCampaignRepository,
) -> None:
    assert await dunning_campaigns.get_active_by_subscription_id(uuid4()) is None


async def test_dunning_close_sets_status_and_ended_at(
    dunning_campaigns: DunningCampaignRepository, session: AsyncSession
) -> None:
    plan = await _make_plan(session)
    sub = await _make_subscription(session, plan)
    created = await dunning_campaigns.create(
        subscription_id=sub.id, invoice_id="in_1", scheduled_job_ids=[]
    )

    closed = await dunning_campaigns.close(created, status="canceled")

    assert closed.status == "canceled"
    assert closed.ended_at is not None


async def test_dunning_set_scheduled_job_ids_updates_ids(
    dunning_campaigns: DunningCampaignRepository, session: AsyncSession
) -> None:
    plan = await _make_plan(session)
    sub = await _make_subscription(session, plan)
    created = await dunning_campaigns.create(
        subscription_id=sub.id, invoice_id="in_1", scheduled_job_ids=[]
    )

    updated = await dunning_campaigns.set_scheduled_job_ids(created, ["job_a", "job_b"])

    assert updated.scheduled_job_ids == ["job_a", "job_b"]
