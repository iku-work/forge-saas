"""
Verifies T-130's send_dunning_email ARQ job (app.workers.jobs.billing): skips
a campaign that's missing or no longer active (already canceled/completed by
DunningService in the meantime), sends the right template with the right
context to every OWNER of the subscription's org for each of the three steps,
writes an audit event per send and is idempotent against a retried run of the
same step, and transitions a still-past_due subscription to "canceled" (plus
closes the campaign) on the day-7 step.

T-234 moved the day-1 and day-3 steps off a direct `send_email` enqueue and
onto NotificationService.send_many, so those two now also reach the in-app
and push channels an owner's preferences ask for -- and, the point of the
change, render each owner's copy in that owner's own language. Day 7 keeps
its own email-only loop (no "subscription_suspended" notification type
exists). Hence the real Redis container below: the service publishes to the
in-app pub/sub channel and busts the unread-count cache.

Same real-Postgres testcontainers + monkeypatched `async_session_factory`
pattern as test_virus_scan_job.py -- the job opens its own session rather
than taking one as a parameter. `send_email`/`send_push` calls go through
tests/conftest.py's autouse `enqueued_jobs` fixture (mocked -- this file
never exercises app.core.jobs.revoke_job, unlike
test_billing_dunning_service.py).
"""

from collections.abc import AsyncGenerator, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

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
from testcontainers.redis import RedisContainer

from app.modules.audit.models import AuditEvent
from app.modules.auth.models import User
from app.modules.auth.permissions import MemberRole
from app.modules.billing.models import DunningCampaign, Plan, Subscription
from app.modules.billing.repository import DunningCampaignRepository
from app.modules.notifications.models import Notification
from app.modules.tenancy.models import Membership, Organization
from app.workers.jobs import billing as billing_job_module
from app.workers.jobs.billing import send_dunning_email
from tests._database import DatabaseHandle

_TABLES = [
    User.__table__,
    Organization.__table__,
    Membership.__table__,
    Plan.__table__,
    Subscription.__table__,
    DunningCampaign.__table__,
    Notification.__table__,
    AuditEvent.__table__,
]


@pytest.fixture(scope="module")
def redis_container() -> "RedisContainer":
    with RedisContainer("redis:7-alpine") as container:
        yield container


@pytest.fixture
async def redis_client(redis_container: "RedisContainer") -> AsyncGenerator[Redis, None]:
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    client: Redis = Redis.from_url(f"redis://{host}:{port}/0")
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture
def ctx(redis_client: Redis) -> dict[str, Any]:
    """The ARQ context the worker passes in; `redis` is what the day-1/day-3
    steps hand to NotificationService (T-234)."""
    return {"redis": redis_client}


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


@pytest.fixture
def session_factory(test_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as s:
        yield s


class _FakeInvoice:
    def __init__(self, amount_due: int, currency: str) -> None:
        self.amount_due = amount_due
        self.currency = currency


@pytest.fixture(autouse=True)
def _fake_stripe_invoice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stripe.Invoice, "retrieve", lambda _invoice_id: _FakeInvoice(4900, "usd"))


async def _make_org_with_owners(
    session: AsyncSession,
    *,
    owners: int = 1,
    members: int = 0,
    owner_locales: Sequence[str] = (),
) -> Organization:
    org = Organization(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    session.add(org)
    await session.flush()

    for i in range(owners):
        user = User(
            email=f"owner{i}-{uuid4().hex[:6]}@example.com",
            full_name=f"Owner {i}",
            locale=owner_locales[i] if i < len(owner_locales) else "en",
        )
        session.add(user)
        await session.flush()
        session.add(Membership(user_id=user.id, org_id=org.id, role=MemberRole.OWNER))

    for i in range(members):
        user = User(email=f"member{i}-{uuid4().hex[:6]}@example.com", full_name=f"Member {i}")
        session.add(user)
        await session.flush()
        session.add(Membership(user_id=user.id, org_id=org.id, role=MemberRole.MEMBER))

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


async def _make_subscription(
    session: AsyncSession, org: Organization, plan: Plan, *, status: str = "past_due"
) -> Subscription:
    subscription = Subscription(
        org_id=org.id,
        plan_id=plan.id,
        stripe_subscription_id=f"sub_{uuid4().hex[:8]}",
        status=status,
        current_period_start=datetime(2026, 1, 1, tzinfo=UTC),
        current_period_end=datetime(2026, 2, 1, tzinfo=UTC),
    )
    session.add(subscription)
    await session.flush()
    return subscription


async def _make_campaign(
    session: AsyncSession, subscription: Subscription, *, status: str = "active"
) -> DunningCampaign:
    campaigns = DunningCampaignRepository(session)
    campaign = await campaigns.create(
        subscription_id=subscription.id, invoice_id="in_1", scheduled_job_ids=[]
    )
    if status != "active":
        await campaigns.close(campaign, status=status)
    await session.commit()
    return campaign


async def test_missing_campaign_is_a_noop(
    ctx: dict[str, Any],
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    await send_dunning_email(ctx, campaign_id=uuid4(), step="day1")

    assert enqueued_jobs == []


async def test_canceled_campaign_is_a_noop(
    session: AsyncSession,
    ctx: dict[str, Any],
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    org = await _make_org_with_owners(session)
    plan = await _make_plan(session)
    sub = await _make_subscription(session, org, plan)
    campaign = await _make_campaign(session, sub, status="canceled")

    await send_dunning_email(ctx, campaign_id=campaign.id, step="day1")

    assert enqueued_jobs == []


async def test_day1_sends_payment_failed_email_to_every_owner_only(
    session: AsyncSession,
    ctx: dict[str, Any],
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    org = await _make_org_with_owners(session, owners=2, members=1)
    plan = await _make_plan(session)
    sub = await _make_subscription(session, org, plan)
    campaign = await _make_campaign(session, sub)

    await send_dunning_email(ctx, campaign_id=campaign.id, step="day1")

    emails = [kwargs for function, kwargs in enqueued_jobs if function == "send_email"]
    # payment_failed's DEFAULT_PREFERENCES enables push too (T-234 routes this
    # step through NotificationService), but never for the plain MEMBER.
    pushes = [kwargs for function, kwargs in enqueued_jobs if function == "send_push"]
    assert len(emails) == 2
    assert len(pushes) == 2
    for kwargs in emails:
        assert kwargs["template"] == "payment_failed"
        assert kwargs["context"]["org_name"] == "Acme"
        assert kwargs["context"]["amount"] == "$49.00"
        assert "billing" in kwargs["context"]["billing_url"]


async def test_day1_localises_each_owners_copy_to_their_own_locale(
    session: AsyncSession,
    ctx: dict[str, Any],
    session_factory: async_sessionmaker[AsyncSession],
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    """T-234's headline case: one event, two owners, two languages.

    The failure mode this guards against is rendering (or resolving a
    locale, or building the CTA link) once above the fan-out loop, which
    would give every owner the first one's language.
    """
    org = await _make_org_with_owners(session, owners=2, owner_locales=("en", "fi"))
    plan = await _make_plan(session)
    sub = await _make_subscription(session, org, plan)
    campaign = await _make_campaign(session, sub)

    await send_dunning_email(ctx, campaign_id=campaign.id, step="day1")

    emails = [kwargs for function, kwargs in enqueued_jobs if function == "send_email"]
    assert sorted(kwargs["locale"] for kwargs in emails) == ["en", "fi"]
    # The link inside each email carries its own recipient's locale segment.
    by_locale = {kwargs["locale"]: kwargs["context"]["billing_url"] for kwargs in emails}
    assert by_locale["en"].endswith("/en/settings/billing")
    assert by_locale["fi"].endswith("/fi/settings/billing")

    async with session_factory() as verify_session:
        rows = (
            await verify_session.execute(text("SELECT title FROM notification ORDER BY title"))
        ).all()
    assert [row.title for row in rows] == ["Maksu epäonnistui", "Payment failed"]


async def test_day1_writes_an_audit_event(
    session: AsyncSession,
    ctx: dict[str, Any],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    org = await _make_org_with_owners(session)
    plan = await _make_plan(session)
    sub = await _make_subscription(session, org, plan)
    campaign = await _make_campaign(session, sub)

    await send_dunning_email(ctx, campaign_id=campaign.id, step="day1")

    async with session_factory() as verify_session:
        row = (
            await verify_session.execute(
                text(
                    "SELECT action, org_id, resource_id FROM audit_event "
                    "WHERE action = 'billing.dunning_email_sent'"
                )
            )
        ).one()
        assert row.resource_id == f"{campaign.id}:day1"
        assert str(row.org_id) == str(org.id)


async def test_day3_sends_subscription_expiring_email(
    session: AsyncSession,
    ctx: dict[str, Any],
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    org = await _make_org_with_owners(session)
    plan = await _make_plan(session)
    sub = await _make_subscription(session, org, plan)
    campaign = await _make_campaign(session, sub)

    await send_dunning_email(ctx, campaign_id=campaign.id, step="day3")

    # subscription_expiring's DEFAULT_PREFERENCES is email + in_app, no push.
    assert len(enqueued_jobs) == 1
    _function, kwargs = enqueued_jobs[0]
    assert kwargs["template"] == "subscription_expiring"
    assert kwargs["context"]["plan_name"] == "Pro"
    assert kwargs["context"]["expires_at"] == "2026-02-01"


async def test_day7_sends_subscription_suspended_email(
    session: AsyncSession,
    ctx: dict[str, Any],
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    org = await _make_org_with_owners(session)
    plan = await _make_plan(session)
    sub = await _make_subscription(session, org, plan)
    campaign = await _make_campaign(session, sub)

    await send_dunning_email(ctx, campaign_id=campaign.id, step="day7")

    assert len(enqueued_jobs) == 1
    _function, kwargs = enqueued_jobs[0]
    assert kwargs["template"] == "subscription_suspended"
    assert kwargs["context"]["plan_name"] == "Pro"


async def test_day7_transitions_still_past_due_subscription_to_canceled(
    session: AsyncSession,
    ctx: dict[str, Any],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    org = await _make_org_with_owners(session)
    plan = await _make_plan(session)
    sub = await _make_subscription(session, org, plan, status="past_due")
    campaign = await _make_campaign(session, sub)

    await send_dunning_email(ctx, campaign_id=campaign.id, step="day7")

    async with session_factory() as verify_session:
        reloaded_sub = await verify_session.get(Subscription, sub.id)
        assert reloaded_sub is not None
        assert reloaded_sub.status == "canceled"

        reloaded_campaign = await DunningCampaignRepository(verify_session).get_by_id(campaign.id)
        assert reloaded_campaign is not None
        assert reloaded_campaign.status == "completed"


async def test_day7_does_not_change_subscription_status_if_already_active(
    session: AsyncSession,
    ctx: dict[str, Any],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    org = await _make_org_with_owners(session)
    plan = await _make_plan(session)
    sub = await _make_subscription(session, org, plan, status="active")
    campaign = await _make_campaign(session, sub)

    await send_dunning_email(ctx, campaign_id=campaign.id, step="day7")

    async with session_factory() as verify_session:
        reloaded_sub = await verify_session.get(Subscription, sub.id)
        assert reloaded_sub is not None
        assert reloaded_sub.status == "active"


async def test_rerunning_the_same_step_does_not_resend(
    session: AsyncSession,
    ctx: dict[str, Any],
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    org = await _make_org_with_owners(session)
    plan = await _make_plan(session)
    sub = await _make_subscription(session, org, plan)
    campaign = await _make_campaign(session, sub)

    await send_dunning_email(ctx, campaign_id=campaign.id, step="day1")
    await send_dunning_email(ctx, campaign_id=campaign.id, step="day1")

    assert [function for function, _ in enqueued_jobs] == ["send_email", "send_push"]
