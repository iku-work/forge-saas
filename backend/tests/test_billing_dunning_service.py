"""
Verifies T-130's DunningService: start() schedules the day-1/day-3/day-7
dunning email sequence as deferred ARQ jobs tracked on a DunningCampaign row,
and cancel()/complete() revoke whichever of those jobs haven't fired yet.

Real Postgres + real Redis here, not tests/conftest.py's autouse
`enqueued_jobs` fixture (which this module overrides by name) -- the "cancel
actually removes a still-queued job" behavior can only be proven against a
real ARQ queue, not a monkeypatched enqueue_job that never touches Redis.
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
from testcontainers.redis import RedisContainer

from app.core import jobs
from app.core.config import get_settings
from app.modules.billing.dunning import DunningService
from app.modules.billing.models import DunningCampaign, Plan, Subscription
from app.modules.billing.repository import DunningCampaignRepository
from tests._database import DatabaseHandle

_TABLES = [Plan.__table__, Subscription.__table__, DunningCampaign.__table__]


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


@pytest.fixture(autouse=True)
def enqueued_jobs(redis_container: RedisContainer, monkeypatch: pytest.MonkeyPatch) -> None:
    # Shadows tests/conftest.py's mocked `enqueued_jobs` fixture (same name,
    # closer scope wins) so DunningService talks to a real ARQ pool here.
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    monkeypatch.setenv("REDIS_URL", f"redis://{host}:{port}/7")
    get_settings.cache_clear()
    jobs._pool = None


@pytest.fixture(autouse=True)
async def _reset_pool() -> AsyncGenerator[None, None]:
    yield
    await jobs.dispose_pool()


@pytest.fixture
def dunning(session: AsyncSession) -> DunningService:
    return DunningService(session)


@pytest.fixture
def campaigns(session: AsyncSession) -> DunningCampaignRepository:
    return DunningCampaignRepository(session)


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


async def test_start_creates_active_campaign_and_schedules_three_jobs(
    dunning: DunningService, session: AsyncSession
) -> None:
    plan = await _make_plan(session)
    sub = await _make_subscription(session, plan)

    campaign = await dunning.start(sub.id, "in_1")

    assert campaign.status == "active"
    assert campaign.subscription_id == sub.id
    assert campaign.invoice_id == "in_1"
    assert len(campaign.scheduled_job_ids) == 3

    queued = await jobs._pool.queued_jobs()
    queued_by_id = {j.job_id: j for j in queued}
    for job_id in campaign.scheduled_job_ids:
        assert job_id in queued_by_id
    steps = {queued_by_id[job_id].kwargs["step"] for job_id in campaign.scheduled_job_ids}
    assert steps == {"day1", "day3", "day7"}


async def test_cancel_marks_canceled_and_revokes_queued_jobs(
    dunning: DunningService, campaigns: DunningCampaignRepository, session: AsyncSession
) -> None:
    plan = await _make_plan(session)
    sub = await _make_subscription(session, plan)
    campaign = await dunning.start(sub.id, "in_1")
    job_ids = list(campaign.scheduled_job_ids)

    await dunning.cancel(sub.id)

    reloaded = await campaigns.get_by_id(campaign.id)
    assert reloaded is not None
    assert reloaded.status == "canceled"
    assert reloaded.ended_at is not None

    queued_ids = {j.job_id for j in await jobs._pool.queued_jobs()}
    assert not (queued_ids & set(job_ids))


async def test_complete_marks_completed_and_revokes_queued_jobs(
    dunning: DunningService, campaigns: DunningCampaignRepository, session: AsyncSession
) -> None:
    plan = await _make_plan(session)
    sub = await _make_subscription(session, plan)
    campaign = await dunning.start(sub.id, "in_1")
    job_ids = list(campaign.scheduled_job_ids)

    await dunning.complete(sub.id)

    reloaded = await campaigns.get_by_id(campaign.id)
    assert reloaded is not None
    assert reloaded.status == "completed"

    queued_ids = {j.job_id for j in await jobs._pool.queued_jobs()}
    assert not (queued_ids & set(job_ids))


async def test_cancel_on_subscription_without_active_campaign_is_a_no_op(
    dunning: DunningService,
) -> None:
    await dunning.cancel(uuid4())


async def test_complete_on_subscription_without_active_campaign_is_a_no_op(
    dunning: DunningService,
) -> None:
    await dunning.complete(uuid4())


async def test_start_while_a_campaign_is_active_cancels_the_previous_one(
    dunning: DunningService, campaigns: DunningCampaignRepository, session: AsyncSession
) -> None:
    plan = await _make_plan(session)
    sub = await _make_subscription(session, plan)
    first = await dunning.start(sub.id, "in_1")
    first_job_ids = list(first.scheduled_job_ids)

    second = await dunning.start(sub.id, "in_2")

    reloaded_first = await campaigns.get_by_id(first.id)
    assert reloaded_first is not None
    assert reloaded_first.status == "canceled"
    assert second.status == "active"
    assert second.invoice_id == "in_2"
    assert second.id != first.id

    queued_ids = {j.job_id for j in await jobs._pool.queued_jobs()}
    assert not (queued_ids & set(first_job_ids))
    assert set(second.scheduled_job_ids) <= queued_ids
