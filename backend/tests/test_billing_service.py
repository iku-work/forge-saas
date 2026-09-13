"""
Verifies T-030's BillingService.handle_event(): dispatches a stored
WebhookEvent by its `type` to the right Subscription mutation, resolves the
target Plan from the Stripe price id embedded in the subscription payload
(never trusts a price/org id the local DB doesn't recognize -- it logs and
no-ops instead), and emits an audit event on payment failure. Same
real-Postgres testcontainers pattern as test_audit_service.py.

Payloads here are hand-built minimal subsets of Stripe's actual event shape
-- only the fields handle_event reads -- rather than full fixture recordings,
since the dispatch logic only ever looks at a handful of keys.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel
from testcontainers.postgres import PostgresContainer

from app.modules.audit.models import AuditEvent
from app.modules.billing.models import DunningCampaign, Plan, Subscription, WebhookEvent
from app.modules.billing.repository import (
    DunningCampaignRepository,
    SubscriptionRepository,
    WebhookEventRepository,
)
from app.modules.billing.service import BillingService
from app.modules.feature_flags.service import FeatureFlagService
from app.modules.tenancy.models import Organization, OrgFeatureFlag
from tests._module_tables import with_module_hot_path_tables

# Wrapped rather than listed: an enabled optional module reaches for tables
# this file has no other reason to know about. See tests/_module_tables.py.
_TABLES = with_module_hot_path_tables(
    Plan.__table__,
    Subscription.__table__,
    WebhookEvent.__table__,
    AuditEvent.__table__,
    Organization.__table__,
    OrgFeatureFlag.__table__,
    DunningCampaign.__table__,
)


@pytest.fixture(scope="module")
def postgres_container() -> "PostgresContainer":
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


# Function-scoped (not module-scoped): see test_audit_repository.py — an
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
def subscriptions(session: AsyncSession) -> SubscriptionRepository:
    return SubscriptionRepository(session)


@pytest.fixture
def webhook_events(session: AsyncSession) -> WebhookEventRepository:
    return WebhookEventRepository(session)


@pytest.fixture
def dunning_campaigns(session: AsyncSession) -> DunningCampaignRepository:
    return DunningCampaignRepository(session)


async def _make_plan(session: AsyncSession, *, price_id: str = "price_1") -> Plan:
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


# The payload shapes below are Basil's (Stripe API >= 2025-03-31.basil), taken
# from real events this integration received on 2026-09-12 under
# 2025-08-27.basil. They used to be the pre-Basil shapes, which is precisely
# why this suite stayed green while production could not process a payment:
# `current_period_start` had moved onto the item and `invoice.subscription`
# under `parent`, and a fixture that still carried the old keys asserted
# against an API that no longer exists. The `_legacy` variants below keep the
# old shape covered, since the reader accepts both.

_PERIOD_START = 1_700_000_000
_PERIOD_END = 1_702_592_000


def _subscription_event(
    *, subscription_id: str, status: str, org_id: str | None, price_id: str
) -> dict[str, Any]:
    return {
        "data": {
            "object": {
                "id": subscription_id,
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
        }
    }


def _subscription_event_legacy(
    *, subscription_id: str, status: str, org_id: str | None, price_id: str
) -> dict[str, Any]:
    """Pre-Basil: the period sat on the subscription, not on its items."""
    return {
        "data": {
            "object": {
                "id": subscription_id,
                "status": status,
                "metadata": {} if org_id is None else {"org_id": org_id},
                "items": {"data": [{"price": {"id": price_id}}]},
                "current_period_start": _PERIOD_START,
                "current_period_end": _PERIOD_END,
                "cancel_at_period_end": False,
                "trial_end": None,
            }
        }
    }


def _invoice_event(*, subscription_id: str | None, invoice_id: str = "in_1") -> dict[str, Any]:
    parent = (
        None
        if subscription_id is None
        else {
            "type": "subscription_details",
            "quote_details": None,
            "subscription_details": {"subscription": subscription_id, "metadata": {}},
        }
    )
    return {"data": {"object": {"id": invoice_id, "parent": parent}}}


def _invoice_event_legacy(
    *, subscription_id: str | None, invoice_id: str = "in_1"
) -> dict[str, Any]:
    """Pre-Basil: the subscription id sat at the top level of the invoice."""
    return {"data": {"object": {"id": invoice_id, "subscription": subscription_id}}}


# --- customer.subscription.created / updated ----------------------------------------


async def test_subscription_created_upserts_subscription(
    service: BillingService, session: AsyncSession, subscriptions: SubscriptionRepository
) -> None:
    plan = await _make_plan(session, price_id="price_pro")
    org_id = uuid4()
    event = WebhookEvent(
        id="evt_1",
        type="customer.subscription.created",
        payload=_subscription_event(
            subscription_id="sub_1",
            status="active",
            org_id=str(org_id),
            price_id="price_pro",
        ),
    )

    await service.handle_event(event)

    sub = await subscriptions.get_by_stripe_subscription_id("sub_1")
    assert sub is not None
    assert sub.org_id == org_id
    assert sub.plan_id == plan.id
    assert sub.status == "active"
    # The period comes off the *item* under Basil. Asserting it is what
    # makes this test fail against the pre-Basil reader rather than merely
    # exercising it.
    assert sub.current_period_start == datetime.fromtimestamp(_PERIOD_START, tz=UTC)
    assert sub.current_period_end == datetime.fromtimestamp(_PERIOD_END, tz=UTC)


async def test_subscription_created_reads_period_from_pre_basil_payload(
    service: BillingService, session: AsyncSession, subscriptions: SubscriptionRepository
) -> None:
    """An account still on a pre-Basil API version sends the period up top."""
    await _make_plan(session, price_id="price_pro")
    await service.handle_event(
        WebhookEvent(
            id="evt_legacy_sub",
            type="customer.subscription.created",
            payload=_subscription_event_legacy(
                subscription_id="sub_legacy",
                status="active",
                org_id=str(uuid4()),
                price_id="price_pro",
            ),
        )
    )

    sub = await subscriptions.get_by_stripe_subscription_id("sub_legacy")
    assert sub is not None
    assert sub.current_period_start == datetime.fromtimestamp(_PERIOD_START, tz=UTC)
    assert sub.current_period_end == datetime.fromtimestamp(_PERIOD_END, tz=UTC)


async def test_subscription_created_without_any_period_raises(
    service: BillingService, session: AsyncSession
) -> None:
    """Neither shape present: fail loudly so Stripe retries.

    The tempting alternative -- log a warning and return -- would mark the
    event processed and leave a paying customer on the free tier with no
    error anywhere, which is the exact failure this module's docstring warns
    about.
    """
    await _make_plan(session, price_id="price_pro")
    payload = _subscription_event(
        subscription_id="sub_no_period",
        status="active",
        org_id=str(uuid4()),
        price_id="price_pro",
    )
    del payload["data"]["object"]["items"]["data"][0]["current_period_start"]
    del payload["data"]["object"]["items"]["data"][0]["current_period_end"]

    with pytest.raises(KeyError, match="current_period_start"):
        await service.handle_event(
            WebhookEvent(id="evt_no_period", type="customer.subscription.created", payload=payload)
        )


async def test_subscription_updated_changes_existing_row_not_create_new(
    service: BillingService, session: AsyncSession, subscriptions: SubscriptionRepository
) -> None:
    plan = await _make_plan(session, price_id="price_pro")
    org_id = uuid4()
    await service.handle_event(
        WebhookEvent(
            id="evt_a",
            type="customer.subscription.created",
            payload=_subscription_event(
                subscription_id="sub_2",
                status="trialing",
                org_id=str(org_id),
                price_id="price_pro",
            ),
        )
    )

    await service.handle_event(
        WebhookEvent(
            id="evt_b",
            type="customer.subscription.updated",
            payload=_subscription_event(
                subscription_id="sub_2",
                status="active",
                org_id=str(org_id),
                price_id="price_pro",
            ),
        )
    )

    sub = await subscriptions.get_by_stripe_subscription_id("sub_2")
    assert sub is not None
    assert sub.plan_id == plan.id
    assert sub.status == "active"
    count = (
        await session.execute(
            text("SELECT count(*) FROM subscription WHERE org_id = :org_id"),
            {"org_id": str(org_id)},
        )
    ).scalar_one()
    assert count == 1


async def test_subscription_created_missing_org_id_is_ignored(
    service: BillingService, session: AsyncSession, subscriptions: SubscriptionRepository
) -> None:
    await _make_plan(session, price_id="price_pro")

    await service.handle_event(
        WebhookEvent(
            id="evt_no_org",
            type="customer.subscription.created",
            payload=_subscription_event(
                subscription_id="sub_no_org",
                status="active",
                org_id=None,
                price_id="price_pro",
            ),
        )
    )

    assert await subscriptions.get_by_stripe_subscription_id("sub_no_org") is None


async def test_subscription_created_unknown_price_is_ignored(
    service: BillingService, subscriptions: SubscriptionRepository
) -> None:
    await service.handle_event(
        WebhookEvent(
            id="evt_unknown_price",
            type="customer.subscription.created",
            payload=_subscription_event(
                subscription_id="sub_unknown_price",
                status="active",
                org_id=str(uuid4()),
                price_id="price_does_not_exist",
            ),
        )
    )

    assert await subscriptions.get_by_stripe_subscription_id("sub_unknown_price") is None


# --- customer.subscription.deleted ---------------------------------------------------


async def test_subscription_deleted_sets_status_canceled(
    service: BillingService, session: AsyncSession, subscriptions: SubscriptionRepository
) -> None:
    await _make_plan(session, price_id="price_pro")
    await service.handle_event(
        WebhookEvent(
            id="evt_create",
            type="customer.subscription.created",
            payload=_subscription_event(
                subscription_id="sub_3",
                status="active",
                org_id=str(uuid4()),
                price_id="price_pro",
            ),
        )
    )

    await service.handle_event(
        WebhookEvent(
            id="evt_delete",
            type="customer.subscription.deleted",
            payload={"data": {"object": {"id": "sub_3"}}},
        )
    )

    sub = await subscriptions.get_by_stripe_subscription_id("sub_3")
    assert sub is not None
    assert sub.status == "canceled"


# --- invoice.payment_succeeded / payment_failed ---------------------------------------


async def test_invoice_payment_succeeded_sets_status_active(
    service: BillingService, session: AsyncSession, subscriptions: SubscriptionRepository
) -> None:
    await _make_plan(session, price_id="price_pro")
    await service.handle_event(
        WebhookEvent(
            id="evt_create",
            type="customer.subscription.created",
            payload=_subscription_event(
                subscription_id="sub_4",
                status="past_due",
                org_id=str(uuid4()),
                price_id="price_pro",
            ),
        )
    )

    await service.handle_event(
        WebhookEvent(
            id="evt_invoice",
            type="invoice.payment_succeeded",
            payload=_invoice_event(subscription_id="sub_4"),
        )
    )

    sub = await subscriptions.get_by_stripe_subscription_id("sub_4")
    assert sub is not None
    assert sub.status == "active"


async def test_invoice_payment_succeeded_without_subscription_field_is_noop(
    service: BillingService,
) -> None:
    await service.handle_event(
        WebhookEvent(
            id="evt_invoice_no_sub",
            type="invoice.payment_succeeded",
            payload=_invoice_event(subscription_id=None),
        )
    )


async def test_invoice_payment_succeeded_reads_pre_basil_subscription_field(
    service: BillingService, session: AsyncSession, subscriptions: SubscriptionRepository
) -> None:
    await _make_plan(session, price_id="price_pro")
    await service.handle_event(
        WebhookEvent(
            id="evt_create_legacy_inv",
            type="customer.subscription.created",
            payload=_subscription_event(
                subscription_id="sub_legacy_inv",
                status="past_due",
                org_id=str(uuid4()),
                price_id="price_pro",
            ),
        )
    )

    await service.handle_event(
        WebhookEvent(
            id="evt_invoice_legacy",
            type="invoice.payment_succeeded",
            payload=_invoice_event_legacy(subscription_id="sub_legacy_inv"),
        )
    )

    sub = await subscriptions.get_by_stripe_subscription_id("sub_legacy_inv")
    assert sub is not None
    assert sub.status == "active"


async def test_invoice_payment_failed_sets_status_past_due_and_emits_audit_event(
    service: BillingService, session: AsyncSession, subscriptions: SubscriptionRepository
) -> None:
    org_id = uuid4()
    await _make_plan(session, price_id="price_pro")
    await service.handle_event(
        WebhookEvent(
            id="evt_create",
            type="customer.subscription.created",
            payload=_subscription_event(
                subscription_id="sub_5",
                status="active",
                org_id=str(org_id),
                price_id="price_pro",
            ),
        )
    )

    await service.handle_event(
        WebhookEvent(
            id="evt_invoice_failed",
            type="invoice.payment_failed",
            payload=_invoice_event(subscription_id="sub_5"),
        )
    )

    sub = await subscriptions.get_by_stripe_subscription_id("sub_5")
    assert sub is not None
    assert sub.status == "past_due"

    audit_row = (
        await session.execute(
            text("SELECT action, org_id FROM audit_event WHERE resource_id = :rid"),
            {"rid": str(sub.id)},
        )
    ).one()
    assert audit_row.action == "billing.payment_failed"
    assert str(audit_row.org_id) == str(org_id)


async def test_invoice_payment_failed_without_subscription_field_is_noop(
    service: BillingService,
) -> None:
    await service.handle_event(
        WebhookEvent(
            id="evt_invoice_failed_no_sub",
            type="invoice.payment_failed",
            payload=_invoice_event(subscription_id=None),
        )
    )


# --- T-133: invoice cache invalidation on invoice.* events -----------------------------


async def test_invoice_payment_succeeded_invalidates_invoice_cache(
    service: BillingService, session: AsyncSession, redis: Redis
) -> None:
    org_id = uuid4()
    await _make_plan(session, price_id="price_invoice_cache_1")
    await service.handle_event(
        WebhookEvent(
            id="evt_create_ic1",
            type="customer.subscription.created",
            payload=_subscription_event(
                subscription_id="sub_ic1",
                status="past_due",
                org_id=str(org_id),
                price_id="price_invoice_cache_1",
            ),
        )
    )
    await redis.set(f"invoices:{org_id}:first", "cached-payload")

    await service.handle_event(
        WebhookEvent(
            id="evt_invoice_succeeded_ic1",
            type="invoice.payment_succeeded",
            payload=_invoice_event(subscription_id="sub_ic1"),
        )
    )

    assert await redis.get(f"invoices:{org_id}:first") is None


async def test_invoice_payment_failed_invalidates_invoice_cache(
    service: BillingService, session: AsyncSession, redis: Redis
) -> None:
    org_id = uuid4()
    await _make_plan(session, price_id="price_invoice_cache_2")
    await service.handle_event(
        WebhookEvent(
            id="evt_create_ic2",
            type="customer.subscription.created",
            payload=_subscription_event(
                subscription_id="sub_ic2",
                status="active",
                org_id=str(org_id),
                price_id="price_invoice_cache_2",
            ),
        )
    )
    await redis.set(f"invoices:{org_id}:first", "cached-payload")

    await service.handle_event(
        WebhookEvent(
            id="evt_invoice_failed_ic2",
            type="invoice.payment_failed",
            payload=_invoice_event(subscription_id="sub_ic2"),
        )
    )

    assert await redis.get(f"invoices:{org_id}:first") is None


# --- T-130: dunning campaign wiring ----------------------------------------------------


async def test_invoice_payment_failed_starts_a_dunning_campaign(
    service: BillingService,
    session: AsyncSession,
    subscriptions: SubscriptionRepository,
    dunning_campaigns: DunningCampaignRepository,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    await _make_plan(session, price_id="price_dunning_1")
    await service.handle_event(
        WebhookEvent(
            id="evt_create",
            type="customer.subscription.created",
            payload=_subscription_event(
                subscription_id="sub_dunning_1",
                status="active",
                org_id=str(uuid4()),
                price_id="price_dunning_1",
            ),
        )
    )

    await service.handle_event(
        WebhookEvent(
            id="evt_invoice_failed",
            type="invoice.payment_failed",
            payload=_invoice_event(subscription_id="sub_dunning_1", invoice_id="in_dunning_1"),
        )
    )

    sub = await subscriptions.get_by_stripe_subscription_id("sub_dunning_1")
    assert sub is not None
    campaign = await dunning_campaigns.get_active_by_subscription_id(sub.id)
    assert campaign is not None
    assert campaign.invoice_id == "in_dunning_1"
    assert len(campaign.scheduled_job_ids) == 3

    dunning_calls = [c for c in enqueued_jobs if c[0] == "send_dunning_email"]
    assert len(dunning_calls) == 3
    assert {kwargs["step"] for _function, kwargs in dunning_calls} == {"day1", "day3", "day7"}


async def test_invoice_payment_succeeded_completes_the_active_dunning_campaign(
    service: BillingService,
    session: AsyncSession,
    subscriptions: SubscriptionRepository,
    dunning_campaigns: DunningCampaignRepository,
) -> None:
    await _make_plan(session, price_id="price_dunning_2")
    await service.handle_event(
        WebhookEvent(
            id="evt_create",
            type="customer.subscription.created",
            payload=_subscription_event(
                subscription_id="sub_dunning_2",
                status="past_due",
                org_id=str(uuid4()),
                price_id="price_dunning_2",
            ),
        )
    )
    await service.handle_event(
        WebhookEvent(
            id="evt_invoice_failed",
            type="invoice.payment_failed",
            payload=_invoice_event(subscription_id="sub_dunning_2"),
        )
    )
    sub = await subscriptions.get_by_stripe_subscription_id("sub_dunning_2")
    assert sub is not None
    campaign = await dunning_campaigns.get_active_by_subscription_id(sub.id)
    assert campaign is not None

    await service.handle_event(
        WebhookEvent(
            id="evt_invoice_succeeded",
            type="invoice.payment_succeeded",
            payload=_invoice_event(subscription_id="sub_dunning_2"),
        )
    )

    reloaded = await dunning_campaigns.get_by_id(campaign.id)
    assert reloaded is not None
    assert reloaded.status == "completed"
    assert await dunning_campaigns.get_active_by_subscription_id(sub.id) is None


async def test_subscription_deleted_cancels_the_active_dunning_campaign(
    service: BillingService,
    session: AsyncSession,
    subscriptions: SubscriptionRepository,
    dunning_campaigns: DunningCampaignRepository,
) -> None:
    await _make_plan(session, price_id="price_dunning_3")
    await service.handle_event(
        WebhookEvent(
            id="evt_create",
            type="customer.subscription.created",
            payload=_subscription_event(
                subscription_id="sub_dunning_3",
                status="past_due",
                org_id=str(uuid4()),
                price_id="price_dunning_3",
            ),
        )
    )
    await service.handle_event(
        WebhookEvent(
            id="evt_invoice_failed",
            type="invoice.payment_failed",
            payload=_invoice_event(subscription_id="sub_dunning_3"),
        )
    )
    sub = await subscriptions.get_by_stripe_subscription_id("sub_dunning_3")
    assert sub is not None
    campaign = await dunning_campaigns.get_active_by_subscription_id(sub.id)
    assert campaign is not None

    await service.handle_event(
        WebhookEvent(
            id="evt_delete",
            type="customer.subscription.deleted",
            payload={"data": {"object": {"id": "sub_dunning_3"}}},
        )
    )

    reloaded = await dunning_campaigns.get_by_id(campaign.id)
    assert reloaded is not None
    assert reloaded.status == "canceled"


async def test_retriggering_payment_failed_cancels_the_previous_campaign(
    service: BillingService,
    session: AsyncSession,
    subscriptions: SubscriptionRepository,
    dunning_campaigns: DunningCampaignRepository,
) -> None:
    await _make_plan(session, price_id="price_dunning_4")
    await service.handle_event(
        WebhookEvent(
            id="evt_create",
            type="customer.subscription.created",
            payload=_subscription_event(
                subscription_id="sub_dunning_4",
                status="past_due",
                org_id=str(uuid4()),
                price_id="price_dunning_4",
            ),
        )
    )
    await service.handle_event(
        WebhookEvent(
            id="evt_invoice_failed_1",
            type="invoice.payment_failed",
            payload=_invoice_event(subscription_id="sub_dunning_4", invoice_id="in_first"),
        )
    )
    sub = await subscriptions.get_by_stripe_subscription_id("sub_dunning_4")
    assert sub is not None
    first_campaign = await dunning_campaigns.get_active_by_subscription_id(sub.id)
    assert first_campaign is not None

    await service.handle_event(
        WebhookEvent(
            id="evt_invoice_failed_2",
            type="invoice.payment_failed",
            payload=_invoice_event(subscription_id="sub_dunning_4", invoice_id="in_second"),
        )
    )

    reloaded_first = await dunning_campaigns.get_by_id(first_campaign.id)
    assert reloaded_first is not None
    assert reloaded_first.status == "canceled"

    second_campaign = await dunning_campaigns.get_active_by_subscription_id(sub.id)
    assert second_campaign is not None
    assert second_campaign.id != first_campaign.id
    assert second_campaign.invoice_id == "in_second"


# --- unknown event types ---------------------------------------------------------------


async def test_unhandled_event_type_is_ignored(service: BillingService) -> None:
    await service.handle_event(
        WebhookEvent(id="evt_ignored", type="customer.created", payload={"data": {"object": {}}})
    )


# --- T-120: feature-flag cache invalidation on subscription changes -------------------


async def _make_org(session: AsyncSession) -> Organization:
    org = Organization(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    session.add(org)
    await session.flush()
    return org


async def test_subscription_upserted_invalidates_feature_flag_cache(
    service: BillingService, session: AsyncSession, redis: Redis
) -> None:
    await _make_plan(session, price_id="price_flags_1")
    org = await _make_org(session)
    await FeatureFlagService(session, redis).is_enabled(org, "seats")
    assert await redis.get(f"feature_flags:{org.id}:seats") is not None

    await service.handle_event(
        WebhookEvent(
            id="evt_flags_upsert",
            type="customer.subscription.created",
            payload=_subscription_event(
                subscription_id="sub_flags_1",
                status="active",
                org_id=str(org.id),
                price_id="price_flags_1",
            ),
        )
    )

    assert await redis.get(f"feature_flags:{org.id}:seats") is None


async def test_subscription_deleted_invalidates_feature_flag_cache(
    service: BillingService, session: AsyncSession, redis: Redis
) -> None:
    await _make_plan(session, price_id="price_flags_2")
    org = await _make_org(session)
    await service.handle_event(
        WebhookEvent(
            id="evt_flags_create",
            type="customer.subscription.created",
            payload=_subscription_event(
                subscription_id="sub_flags_2",
                status="active",
                org_id=str(org.id),
                price_id="price_flags_2",
            ),
        )
    )
    await FeatureFlagService(session, redis).is_enabled(org, "seats")
    assert await redis.get(f"feature_flags:{org.id}:seats") is not None

    await service.handle_event(
        WebhookEvent(
            id="evt_flags_delete",
            type="customer.subscription.deleted",
            payload={"data": {"object": {"id": "sub_flags_2"}}},
        )
    )

    assert await redis.get(f"feature_flags:{org.id}:seats") is None


# --- duplicate subscriptions and other unapplied events -------------------------------


async def test_second_live_subscription_for_one_org_is_recorded_not_applied(
    service: BillingService, session: AsyncSession, subscriptions: SubscriptionRepository
) -> None:
    """Two Stripe subscriptions on one org must not crash the webhook.

    `subscription.org_id` is unique and `upsert` looks rows up by *Stripe*
    id, so before this the second one took the INSERT path and raised
    UniqueViolation -- a 500 that Stripe retried into the same constraint
    forever. It is now skipped, and the org keeps the subscription it
    already had.
    """
    await _make_plan(session, price_id="price_pro")
    org_id = uuid4()
    for stripe_id in ("sub_original", "sub_duplicate"):
        await service.handle_event(
            WebhookEvent(
                id=f"evt_{stripe_id}",
                type="customer.subscription.created",
                payload=_subscription_event(
                    subscription_id=stripe_id,
                    status="active",
                    org_id=str(org_id),
                    price_id="price_pro",
                ),
            )
        )

    sub = await subscriptions.get_by_org_id(org_id)
    assert sub is not None
    assert sub.stripe_subscription_id == "sub_original"
    assert await subscriptions.get_by_stripe_subscription_id("sub_duplicate") is None


async def test_duplicate_subscription_is_audited(
    service: BillingService, session: AsyncSession
) -> None:
    """The skip has to be findable later, or it is just a silent drop."""
    await _make_plan(session, price_id="price_pro")
    org_id = uuid4()
    for stripe_id in ("sub_a", "sub_b"):
        await service.handle_event(
            WebhookEvent(
                id=f"evt_{stripe_id}",
                type="customer.subscription.created",
                payload=_subscription_event(
                    subscription_id=stripe_id,
                    status="active",
                    org_id=str(org_id),
                    price_id="price_pro",
                ),
            )
        )

    rows = (
        await session.execute(
            text(
                "select action, resource_id from audit_event "
                "where action like 'billing.event_not_applied%' and org_id = :org"
            ),
            {"org": org_id},
        )
    ).all()
    assert [(r[0], r[1]) for r in rows] == [
        ("billing.event_not_applied.duplicate_subscription", "sub_b")
    ]


async def test_resubscribe_after_cancellation_adopts_the_new_subscription(
    service: BillingService, session: AsyncSession, subscriptions: SubscriptionRepository
) -> None:
    """A dead row is not a duplicate -- it is an ordinary resubscribe.

    This is the path that would still have hit the unique constraint if
    `upsert` only ever looked rows up by Stripe id.
    """
    await _make_plan(session, price_id="price_pro")
    org_id = uuid4()
    await service.handle_event(
        WebhookEvent(
            id="evt_first",
            type="customer.subscription.created",
            payload=_subscription_event(
                subscription_id="sub_old",
                status="active",
                org_id=str(org_id),
                price_id="price_pro",
            ),
        )
    )
    await service.handle_event(
        WebhookEvent(
            id="evt_gone",
            type="customer.subscription.deleted",
            payload=_subscription_event(
                subscription_id="sub_old",
                status="canceled",
                org_id=str(org_id),
                price_id="price_pro",
            ),
        )
    )
    await service.handle_event(
        WebhookEvent(
            id="evt_again",
            type="customer.subscription.created",
            payload=_subscription_event(
                subscription_id="sub_new",
                status="active",
                org_id=str(org_id),
                price_id="price_pro",
            ),
        )
    )

    sub = await subscriptions.get_by_org_id(org_id)
    assert sub is not None
    assert sub.stripe_subscription_id == "sub_new"
    assert sub.status == "active"


async def test_unknown_price_is_audited_rather_than_silently_dropped(
    service: BillingService, session: AsyncSession
) -> None:
    """Somebody paid for a price this deployment cannot resolve to a plan.

    Previously a bare logger.warning: the endpoint answered 200, Stripe
    considered it delivered, and the org silently stayed on free.
    """
    await _make_plan(session, price_id="price_pro")
    org_id = uuid4()
    await service.handle_event(
        WebhookEvent(
            id="evt_unknown_price",
            type="customer.subscription.created",
            payload=_subscription_event(
                subscription_id="sub_x",
                status="active",
                org_id=str(org_id),
                price_id="price_not_in_this_catalogue",
            ),
        )
    )

    actions = [
        r[0]
        for r in (
            await session.execute(
                text("select action from audit_event where org_id = :org"), {"org": org_id}
            )
        ).all()
    ]
    assert "billing.event_not_applied.unknown_price" in actions


# --- out-of-order delivery ---------------------------------------------------------------
#
# Stripe promises delivery, not order, and its own retries make disorder the
# common case: an event that failed once is redelivered minutes later, behind
# everything that arrived in the meantime.


def _with_created(payload: dict[str, Any], epoch: int) -> dict[str, Any]:
    """Stamp the Event envelope's own `created`, as Stripe sends it."""
    return {**payload, "created": epoch}


_EARLIER = 1_700_000_000
_LATER = 1_700_000_600


async def test_a_redelivered_payment_failed_does_not_undo_a_later_success(
    service: BillingService, session: AsyncSession, subscriptions: SubscriptionRepository
) -> None:
    """The failure this guard exists for.

    A `payment_failed` that Stripe retries arrives after the
    `payment_succeeded` that resolved it. Applied in arrival order, it puts a
    customer who has paid back into dunning and past_due.
    """
    await _make_plan(session, price_id="price_pro")
    org_id = uuid4()
    await service.handle_event(
        WebhookEvent(
            id="evt_created",
            type="customer.subscription.created",
            payload=_with_created(
                _subscription_event(
                    subscription_id="sub_order",
                    status="active",
                    org_id=str(org_id),
                    price_id="price_pro",
                ),
                _EARLIER,
            ),
        )
    )
    await service.handle_event(
        WebhookEvent(
            id="evt_paid",
            type="invoice.payment_succeeded",
            payload=_with_created(_invoice_event(subscription_id="sub_order"), _LATER),
        )
    )
    # ... and only now the retry of the older failure lands.
    await service.handle_event(
        WebhookEvent(
            id="evt_failed_retry",
            type="invoice.payment_failed",
            payload=_with_created(_invoice_event(subscription_id="sub_order"), _EARLIER),
        )
    )

    sub = await subscriptions.get_by_stripe_subscription_id("sub_order")
    assert sub is not None
    assert sub.status == "active"


async def test_events_in_order_are_all_applied(
    service: BillingService, session: AsyncSession, subscriptions: SubscriptionRepository
) -> None:
    """The guard must not reject ordinary forward progress."""
    await _make_plan(session, price_id="price_pro")
    org_id = uuid4()
    await service.handle_event(
        WebhookEvent(
            id="evt_c",
            type="customer.subscription.created",
            payload=_with_created(
                _subscription_event(
                    subscription_id="sub_fwd",
                    status="active",
                    org_id=str(org_id),
                    price_id="price_pro",
                ),
                _EARLIER,
            ),
        )
    )
    await service.handle_event(
        WebhookEvent(
            id="evt_f",
            type="invoice.payment_failed",
            payload=_with_created(_invoice_event(subscription_id="sub_fwd"), _LATER),
        )
    )

    sub = await subscriptions.get_by_stripe_subscription_id("sub_fwd")
    assert sub is not None
    assert sub.status == "past_due"


async def test_an_event_with_no_created_is_still_applied(
    service: BillingService, session: AsyncSession, subscriptions: SubscriptionRepository
) -> None:
    """Every fixture in this file predates the guard and carries no
    `created`; none of them should start being skipped."""
    await _make_plan(session, price_id="price_pro")
    await service.handle_event(
        WebhookEvent(
            id="evt_nocreated",
            type="customer.subscription.created",
            payload=_subscription_event(
                subscription_id="sub_nocreated",
                status="active",
                org_id=str(uuid4()),
                price_id="price_pro",
            ),
        )
    )

    sub = await subscriptions.get_by_stripe_subscription_id("sub_nocreated")
    assert sub is not None
    assert sub.last_stripe_event_at is None


# --- Basil moved the cancellation flag too -----------------------------------------------


async def test_a_subscription_cancelling_at_period_end_is_recorded_as_such(
    service: BillingService, session: AsyncSession, subscriptions: SubscriptionRepository
) -> None:
    """Under Basil the old flag stays False and `cancel_at` carries the truth.

    Taken from two real production subscriptions on 2026-09-13, both
    scheduled to stop on 2026-10-12: `cancel_at_period_end` was False on
    each. Reading only that flag told a customer who had just cancelled that
    their subscription would renew.
    """
    await _make_plan(session, price_id="price_pro")
    payload = _subscription_event(
        subscription_id="sub_ending",
        status="active",
        org_id=str(uuid4()),
        price_id="price_pro",
    )
    payload["data"]["object"]["cancel_at_period_end"] = False
    payload["data"]["object"]["cancel_at"] = _PERIOD_END

    await service.handle_event(
        WebhookEvent(id="evt_cancel_at", type="customer.subscription.updated", payload=payload)
    )

    sub = await subscriptions.get_by_stripe_subscription_id("sub_ending")
    assert sub is not None
    assert sub.cancel_at_period_end is True


async def test_the_pre_basil_cancellation_flag_still_counts(
    service: BillingService, session: AsyncSession, subscriptions: SubscriptionRepository
) -> None:
    await _make_plan(session, price_id="price_pro")
    payload = _subscription_event(
        subscription_id="sub_legacy_cancel",
        status="active",
        org_id=str(uuid4()),
        price_id="price_pro",
    )
    payload["data"]["object"]["cancel_at_period_end"] = True

    await service.handle_event(
        WebhookEvent(id="evt_legacy_cancel", type="customer.subscription.updated", payload=payload)
    )

    sub = await subscriptions.get_by_stripe_subscription_id("sub_legacy_cancel")
    assert sub is not None
    assert sub.cancel_at_period_end is True


async def test_a_renewing_subscription_is_not_marked_as_cancelling(
    service: BillingService, session: AsyncSession, subscriptions: SubscriptionRepository
) -> None:
    """The guard against the obvious over-correction: `cancel_at` absent and
    the flag false has to stay false, or every subscription reads as ending."""
    await _make_plan(session, price_id="price_pro")
    await service.handle_event(
        WebhookEvent(
            id="evt_renewing",
            type="customer.subscription.updated",
            payload=_subscription_event(
                subscription_id="sub_renewing",
                status="active",
                org_id=str(uuid4()),
                price_id="price_pro",
            ),
        )
    )

    sub = await subscriptions.get_by_stripe_subscription_id("sub_renewing")
    assert sub is not None
    assert sub.cancel_at_period_end is False
