"""
Verifies app.workers.jobs.billing.reconcile_subscriptions.

Why this job exists: webhooks are not a reliable transport, and this
deployment proved it on 2026-09-12. A bug made the endpoint 500, Stripe's
retries ran out, and a customer who had paid sat on the free tier. Stripe
also disables an endpoint that keeps failing, after which events are not
retried at all -- a second payment left no local trace whatsoever. The sweep
re-derives local state from Stripe, which is the system of record for what
somebody is actually being charged.

Same real-Postgres testcontainers + monkeypatched `async_session_factory`
pattern as test_trial_notifications_job.py: the job opens its own session
rather than taking one.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
import stripe
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

from app.models._mixins import utcnow
from app.modules.audit.models import AuditEvent
from app.modules.billing.models import DunningCampaign, Plan, Subscription
from app.modules.billing.repository import SubscriptionRepository
from app.modules.tenancy.models import Organization, OrgFeatureFlag
from app.workers.jobs import billing as billing_job_module
from app.workers.jobs.billing import reconcile_subscriptions
from tests._database import DatabaseHandle

_TABLES = [
    Organization.__table__,
    Plan.__table__,
    Subscription.__table__,
    # The sweep cancels any active campaign when it retires a subscription,
    # so this table is on its hot path even though no test here starts one.
    DunningCampaign.__table__,
    AuditEvent.__table__,
    OrgFeatureFlag.__table__,
]

_PERIOD_START = 1_700_000_000
_PERIOD_END = 1_702_592_000


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
    monkeypatch.setattr(billing_job_module, "async_session_factory", factory)


@pytest.fixture(autouse=True)
def _stripe_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The job no-ops without a key; every test here wants it to run."""
    monkeypatch.setattr(
        billing_job_module.get_settings(), "STRIPE_SECRET_KEY", "sk_test_reconcile", raising=False
    )


@pytest.fixture
async def session(test_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s


@pytest.fixture
def redis(_redis_client: Redis) -> Redis:
    return _redis_client


def _stripe_subscription(
    *, subscription_id: str, status: str, price_id: str, org_id: str | None = None
) -> dict[str, Any]:
    """A Stripe Subscription in the shape the API returns under Basil."""
    return {
        "id": subscription_id,
        "object": "subscription",
        "status": status,
        "metadata": {} if org_id is None else {"org_id": org_id},
        "items": {
            "data": [
                {
                    "price": {"id": price_id},
                    "current_period_start": _PERIOD_START,
                    "current_period_end": _PERIOD_END,
                }
            ]
        },
        "cancel_at_period_end": False,
        "trial_end": None,
    }


class _FakeListing:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        # The real SDK hands back StripeObjects; the job calls .to_dict on
        # each, so the fake needs the same one method.
        self.data = [_FakeStripeObject(item) for item in items]


class _FakeStripeObject:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def to_dict(self, *, for_json: bool = False) -> dict[str, Any]:  # noqa: ARG002
        return self._payload


@pytest.fixture
def stripe_subscriptions(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[dict[str, Any]]]:
    """customer id -> the subscriptions Stripe reports for it."""
    by_customer: dict[str, list[dict[str, Any]]] = {}

    async def _list(**kwargs: object) -> _FakeListing:
        return _FakeListing(by_customer.get(str(kwargs["customer"]), []))

    monkeypatch.setattr(stripe.Subscription, "list_async", _list)
    return by_customer


async def _make_plan(session: AsyncSession, price_id: str = "price_pro") -> Plan:
    plan = Plan(
        name="Pro",
        slug=f"pro-{uuid4().hex[:8]}",
        stripe_price_id_monthly=price_id,
        stripe_price_id_yearly=f"{price_id}_yearly",
        price_monthly=2900,
        price_yearly=29000,
        features={"seats": 5},
    )
    session.add(plan)
    await session.flush()
    return plan


async def _make_org(session: AsyncSession, customer_id: str) -> Organization:
    org = Organization(name="Acme", slug=f"acme-{uuid4().hex[:8]}", stripe_customer_id=customer_id)
    session.add(org)
    await session.flush()
    return org


async def _existing_subscription(
    session: AsyncSession, org: Organization, plan: Plan, *, stripe_id: str, status: str
) -> None:
    session.add(
        Subscription(
            org_id=org.id,
            plan_id=plan.id,
            stripe_subscription_id=stripe_id,
            status=status,
            current_period_start=datetime(2026, 1, 1, tzinfo=UTC),
            current_period_end=datetime(2026, 2, 1, tzinfo=UTC),
            cancel_at_period_end=False,
            trial_end=None,
        )
    )
    await session.flush()


async def _audit_actions(session: AsyncSession, org_id: UUID) -> list[str]:
    rows = (
        await session.execute(
            text("select action from audit_event where org_id = :org"), {"org": org_id}
        )
    ).all()
    return [r[0] for r in rows]


# --- the missed-webhook case ------------------------------------------------------------


async def test_applies_a_subscription_the_webhook_never_delivered(
    session: AsyncSession,
    redis: Redis,
    stripe_subscriptions: dict[str, list[dict[str, Any]]],
) -> None:
    """The exact production failure: paid at Stripe, nothing locally."""
    plan = await _make_plan(session)
    org = await _make_org(session, "cus_missed")
    await session.commit()
    # Read before expire_all() below: an expired instance re-loads on
    # attribute access, which from sync code is a MissingGreenlet.
    org_id = org.id
    plan_id = plan.id
    stripe_subscriptions["cus_missed"] = [
        _stripe_subscription(subscription_id="sub_paid", status="active", price_id="price_pro")
    ]

    await reconcile_subscriptions({"redis": redis})

    session.expire_all()
    sub = await SubscriptionRepository(session).get_by_org_id(org_id)
    assert sub is not None
    assert sub.stripe_subscription_id == "sub_paid"
    assert sub.status == "active"
    assert sub.plan_id == plan_id
    # The Stripe object carried no org_id metadata (it was created outside
    # checkout); the job knows the org from the customer and fills it in.
    assert sub.current_period_start == datetime.fromtimestamp(_PERIOD_START, tz=UTC)


async def test_repairs_a_status_that_drifted(
    session: AsyncSession,
    redis: Redis,
    stripe_subscriptions: dict[str, list[dict[str, Any]]],
) -> None:
    plan = await _make_plan(session)
    org = await _make_org(session, "cus_drift")
    await _existing_subscription(session, org, plan, stripe_id="sub_drift", status="active")
    await session.commit()
    org_id = org.id
    stripe_subscriptions["cus_drift"] = [
        _stripe_subscription(subscription_id="sub_drift", status="past_due", price_id="price_pro")
    ]

    await reconcile_subscriptions({"redis": redis})

    session.expire_all()
    sub = await SubscriptionRepository(session).get_by_org_id(org_id)
    assert sub is not None
    assert sub.status == "past_due"


async def test_cancels_a_local_subscription_stripe_no_longer_bills(
    session: AsyncSession,
    redis: Redis,
    stripe_subscriptions: dict[str, list[dict[str, Any]]],
) -> None:
    """A plan granted to somebody nobody is charging is the expensive
    direction of this drift."""
    plan = await _make_plan(session)
    org = await _make_org(session, "cus_gone")
    await _existing_subscription(session, org, plan, stripe_id="sub_gone", status="active")
    await session.commit()
    org_id = org.id
    stripe_subscriptions["cus_gone"] = []

    await reconcile_subscriptions({"redis": redis})

    session.expire_all()
    sub = await SubscriptionRepository(session).get_by_org_id(org_id)
    assert sub is not None
    assert sub.status == "canceled"


async def test_two_live_stripe_subscriptions_are_reported_not_guessed_at(
    session: AsyncSession,
    redis: Redis,
    stripe_subscriptions: dict[str, list[dict[str, Any]]],
) -> None:
    """Exactly the production incident. Picking one automatically could move
    the org onto whichever an operator is about to refund, so it is recorded
    for a human instead."""
    plan = await _make_plan(session)
    org = await _make_org(session, "cus_double")
    await _existing_subscription(session, org, plan, stripe_id="sub_one", status="active")
    await session.commit()
    org_id = org.id
    stripe_subscriptions["cus_double"] = [
        _stripe_subscription(subscription_id="sub_one", status="active", price_id="price_pro"),
        _stripe_subscription(subscription_id="sub_two", status="active", price_id="price_pro"),
    ]

    await reconcile_subscriptions({"redis": redis})

    session.expire_all()
    sub = await SubscriptionRepository(session).get_by_org_id(org_id)
    assert sub is not None
    assert sub.stripe_subscription_id == "sub_one"
    assert "billing.event_not_applied.duplicate_subscription" in await _audit_actions(
        session, org_id
    )


async def test_a_matching_org_is_left_alone(
    session: AsyncSession,
    redis: Redis,
    stripe_subscriptions: dict[str, list[dict[str, Any]]],
) -> None:
    """The common case must be a no-op, or every tick rewrites every row."""
    plan = await _make_plan(session)
    org = await _make_org(session, "cus_ok")
    await _existing_subscription(session, org, plan, stripe_id="sub_ok", status="active")
    await session.commit()
    org_id = org.id
    stripe_subscriptions["cus_ok"] = [
        _stripe_subscription(subscription_id="sub_ok", status="active", price_id="price_pro")
    ]

    await reconcile_subscriptions({"redis": redis})

    assert await _audit_actions(session, org_id) == []


async def test_one_unreachable_customer_does_not_abandon_the_sweep(
    session: AsyncSession,
    redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Stripe outage on org A must not leave org B unreconciled."""
    plan = await _make_plan(session)
    broken = await _make_org(session, "cus_broken")
    healthy = await _make_org(session, "cus_healthy")
    await session.commit()
    broken_id, healthy_id, plan_id = broken.id, healthy.id, plan.id

    async def _list(**kwargs: object) -> _FakeListing:
        if str(kwargs["customer"]) == "cus_broken":
            raise stripe.APIConnectionError("boom")
        return _FakeListing(
            [
                _stripe_subscription(
                    subscription_id="sub_healthy", status="active", price_id="price_pro"
                )
            ]
        )

    monkeypatch.setattr(stripe.Subscription, "list_async", _list)

    await reconcile_subscriptions({"redis": redis})

    session.expire_all()
    repo = SubscriptionRepository(session)
    assert await repo.get_by_org_id(broken_id) is None
    healthy_sub = await repo.get_by_org_id(healthy_id)
    assert healthy_sub is not None
    assert healthy_sub.plan_id == plan_id


async def test_reconciliation_advances_the_staleness_watermark(
    session: AsyncSession,
    redis: Redis,
    stripe_subscriptions: dict[str, list[dict[str, Any]]],
) -> None:
    """A repair must not be undone by a redelivery queued behind it.

    While an endpoint is disabled, Stripe accumulates events. The sweep
    repairs state from the API in the meantime; when the endpoint comes back
    those older events arrive and, without a watermark advance, would
    overwrite the newer truth the sweep had just written.
    """
    await _make_plan(session)
    org = await _make_org(session, "cus_watermark")
    await session.commit()
    org_id = org.id
    stripe_subscriptions["cus_watermark"] = [
        _stripe_subscription(subscription_id="sub_wm", status="active", price_id="price_pro")
    ]

    before = utcnow()
    await reconcile_subscriptions({"redis": redis})

    session.expire_all()
    sub = await SubscriptionRepository(session).get_by_org_id(org_id)
    assert sub is not None
    assert sub.last_stripe_event_at is not None
    assert sub.last_stripe_event_at >= before
