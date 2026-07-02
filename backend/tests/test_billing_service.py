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
from typing import Any
from uuid import uuid4

import pytest
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
from app.modules.billing.models import Plan, Subscription, WebhookEvent
from app.modules.billing.repository import SubscriptionRepository, WebhookEventRepository
from app.modules.billing.service import BillingService

_TABLES = [Plan.__table__, Subscription.__table__, WebhookEvent.__table__, AuditEvent.__table__]


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
def service(session: AsyncSession) -> BillingService:
    return BillingService(session)


@pytest.fixture
def subscriptions(session: AsyncSession) -> SubscriptionRepository:
    return SubscriptionRepository(session)


@pytest.fixture
def webhook_events(session: AsyncSession) -> WebhookEventRepository:
    return WebhookEventRepository(session)


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


def _subscription_event(
    *, subscription_id: str, status: str, org_id: str | None, price_id: str
) -> dict[str, Any]:
    return {
        "data": {
            "object": {
                "id": subscription_id,
                "status": status,
                "metadata": {} if org_id is None else {"org_id": org_id},
                "items": {"data": [{"price": {"id": price_id}}]},
                "current_period_start": 1_700_000_000,
                "current_period_end": 1_702_592_000,
                "cancel_at_period_end": False,
                "trial_end": None,
            }
        }
    }


def _invoice_event(*, subscription_id: str | None) -> dict[str, Any]:
    return {"data": {"object": {"subscription": subscription_id}}}


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


# --- unknown event types ---------------------------------------------------------------


async def test_unhandled_event_type_is_ignored(service: BillingService) -> None:
    await service.handle_event(
        WebhookEvent(id="evt_ignored", type="customer.created", payload={"data": {"object": {}}})
    )
