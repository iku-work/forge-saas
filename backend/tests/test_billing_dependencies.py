"""
Verifies T-031's plan-gating dependency: get_current_org_subscription
resolves the org's currently entitled Plan from its Subscription row
(active/trialing are always entitled; past_due is entitled only inside the
7-day dunning grace window measured from current_period_end; anything else
-- including no Subscription row at all -- falls back to the in-memory free
plan, which has no features), and require_feature raises PlanLimitError
(402) when the resolved plan lacks the requested feature or falls under a
declared minimum.

Called directly rather than through FastAPI/HTTP, mirroring
test_dependencies.py's approach for app.core.dependencies -- get_current_org
itself isn't under test here, so org_and_membership is constructed by hand
instead of resolved through a real membership lookup. Same real-Postgres
testcontainers pattern as test_billing_service.py, since
get_current_org_subscription does real Plan/Subscription queries.

T-065: require_feature's checker takes (org_and_membership, session) -- the
same shape as get_current_org_subscription -- rather than depending on
get_current_org_subscription directly, so it can check is_enabled("billing")
and return None *before* any Plan/Subscription query runs (those tables
don't exist when billing is disabled, see test_require_feature_is_noop_when_billing_disabled).

T-120: the checker's boolean gate now delegates to FeatureFlagService (a
third `redis` param on the checker), so an OrgFeatureFlag override can deny
a feature the plan otherwise grants -- see the "override wins" tests below.
"""

from collections.abc import AsyncGenerator, Generator
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel
from testcontainers.postgres import PostgresContainer

from app.core import module_loader
from app.core.exceptions import PlanLimitError
from app.core.modules import EnabledModules, ModuleConfig
from app.models._mixins import utcnow
from app.modules.auth.permissions import MemberRole
from app.modules.billing.dependencies import (
    get_current_org_subscription,
    record_usage,
    require_feature,
)
from app.modules.billing.models import Plan, Subscription
from app.modules.billing.usage_models import UsageRecord
from app.modules.billing.usage_service import UsageService
from app.modules.feature_flags.repository import OrgFeatureFlagRepository
from app.modules.tenancy.models import Membership, Organization, OrgFeatureFlag
from product import config as product_config

_TABLES = [
    Plan.__table__,
    Subscription.__table__,
    Organization.__table__,
    OrgFeatureFlag.__table__,
    # T-132: record_usage writes here.
    UsageRecord.__table__,
]


@pytest.fixture(autouse=True)
def _clear_modules_cache() -> Generator[None]:
    module_loader.get_modules.cache_clear()
    yield
    module_loader.get_modules.cache_clear()


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


def _org() -> Organization:
    return Organization(name="Acme", slug=f"acme-{uuid4().hex[:8]}")


def _membership(org_id: UUID) -> Membership:
    return Membership(user_id=uuid4(), org_id=org_id, role=MemberRole.OWNER)


async def _make_plan(session: AsyncSession, *, features: dict[str, int]) -> Plan:
    plan = Plan(
        name="Pro",
        slug=f"pro-{uuid4().hex[:8]}",
        stripe_price_id_monthly="price_m",
        stripe_price_id_yearly="price_y",
        price_monthly=2900,
        price_yearly=29000,
        features=features,
    )
    session.add(plan)
    await session.flush()
    return plan


async def _make_subscription(
    session: AsyncSession,
    *,
    org_id: UUID,
    plan_id: UUID,
    status: str,
    current_period_end: datetime,
) -> Subscription:
    sub = Subscription(
        org_id=org_id,
        plan_id=plan_id,
        stripe_subscription_id=f"sub_{uuid4().hex[:8]}",
        status=status,
        current_period_start=utcnow() - timedelta(days=30),
        current_period_end=current_period_end,
    )
    session.add(sub)
    await session.flush()
    return sub


# --- get_current_org_subscription ----------------------------------------------------


async def test_no_subscription_resolves_to_free_plan(session: AsyncSession, redis: Redis) -> None:
    org = _org()

    org_sub = await get_current_org_subscription(
        org_and_membership=(org, _membership(org.id)), session=session, redis=redis
    )

    assert org_sub.subscription is None
    assert org_sub.plan.slug == "free"
    assert org_sub.plan.features == {}


async def test_active_subscription_resolves_to_its_plan(
    session: AsyncSession, redis: Redis
) -> None:
    org = _org()
    plan = await _make_plan(session, features={"seats": 5})
    await _make_subscription(
        session,
        org_id=org.id,
        plan_id=plan.id,
        status="active",
        current_period_end=utcnow() + timedelta(days=20),
    )

    org_sub = await get_current_org_subscription(
        org_and_membership=(org, _membership(org.id)), session=session, redis=redis
    )

    assert org_sub.plan.id == plan.id


async def test_trialing_subscription_is_entitled(session: AsyncSession, redis: Redis) -> None:
    org = _org()
    plan = await _make_plan(session, features={"seats": 5})
    await _make_subscription(
        session,
        org_id=org.id,
        plan_id=plan.id,
        status="trialing",
        current_period_end=utcnow() + timedelta(days=10),
    )

    org_sub = await get_current_org_subscription(
        org_and_membership=(org, _membership(org.id)), session=session, redis=redis
    )

    assert org_sub.plan.id == plan.id


async def test_past_due_within_grace_period_is_entitled(
    session: AsyncSession, redis: Redis
) -> None:
    org = _org()
    plan = await _make_plan(session, features={"seats": 5})
    await _make_subscription(
        session,
        org_id=org.id,
        plan_id=plan.id,
        status="past_due",
        current_period_end=utcnow() - timedelta(days=3),
    )

    org_sub = await get_current_org_subscription(
        org_and_membership=(org, _membership(org.id)), session=session, redis=redis
    )

    assert org_sub.plan.id == plan.id


async def test_past_due_beyond_grace_period_falls_back_to_free_plan(
    session: AsyncSession, redis: Redis
) -> None:
    org = _org()
    plan = await _make_plan(session, features={"seats": 5})
    await _make_subscription(
        session,
        org_id=org.id,
        plan_id=plan.id,
        status="past_due",
        current_period_end=utcnow() - timedelta(days=8),
    )

    org_sub = await get_current_org_subscription(
        org_and_membership=(org, _membership(org.id)), session=session, redis=redis
    )

    assert org_sub.plan.slug == "free"


async def test_canceled_subscription_falls_back_to_free_plan(
    session: AsyncSession, redis: Redis
) -> None:
    org = _org()
    plan = await _make_plan(session, features={"seats": 5})
    await _make_subscription(
        session,
        org_id=org.id,
        plan_id=plan.id,
        status="canceled",
        current_period_end=utcnow() - timedelta(days=1),
    )

    org_sub = await get_current_org_subscription(
        org_and_membership=(org, _membership(org.id)), session=session, redis=redis
    )

    assert org_sub.plan.slug == "free"


# --- require_feature --------------------------------------------------------------------

# Same dependents-must-go-too caveat as test_api_router.py's _BILLING_OFF:
# notifications/file_storage both `requires=["billing"]`.
_BILLING_OFF = EnabledModules(
    billing=ModuleConfig(enabled=False),
    notifications=ModuleConfig(enabled=False),
    file_storage=ModuleConfig(enabled=False),
)


async def test_require_feature_denies_free_plan(session: AsyncSession, redis: Redis) -> None:
    org = _org()
    checker = require_feature("advanced_reports")

    with pytest.raises(PlanLimitError) as exc_info:
        await checker((org, _membership(org.id)), session, redis)
    assert exc_info.value.status_code == 402


async def test_require_feature_allows_when_plan_has_feature(
    session: AsyncSession, redis: Redis
) -> None:
    org = _org()
    plan = await _make_plan(session, features={"seats": 5})
    await _make_subscription(
        session,
        org_id=org.id,
        plan_id=plan.id,
        status="active",
        current_period_end=utcnow() + timedelta(days=20),
    )
    checker = require_feature("seats")

    result = await checker((org, _membership(org.id)), session, redis)

    assert result is not None
    assert result.plan.id == plan.id


async def test_require_feature_below_minimum_raises(session: AsyncSession, redis: Redis) -> None:
    org = _org()
    plan = await _make_plan(session, features={"seats": 3})
    await _make_subscription(
        session,
        org_id=org.id,
        plan_id=plan.id,
        status="active",
        current_period_end=utcnow() + timedelta(days=20),
    )
    checker = require_feature("seats", minimum=5)

    with pytest.raises(PlanLimitError) as exc_info:
        await checker((org, _membership(org.id)), session, redis)
    assert exc_info.value.status_code == 402


async def test_require_feature_meets_minimum_allows(session: AsyncSession, redis: Redis) -> None:
    org = _org()
    plan = await _make_plan(session, features={"seats": 5})
    await _make_subscription(
        session,
        org_id=org.id,
        plan_id=plan.id,
        status="active",
        current_period_end=utcnow() + timedelta(days=20),
    )
    checker = require_feature("seats", minimum=5)

    result = await checker((org, _membership(org.id)), session, redis)

    assert result is not None
    assert result.plan.id == plan.id


async def test_require_feature_is_noop_when_billing_disabled(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession, redis: Redis
) -> None:
    monkeypatch.setattr(product_config, "MODULES", _BILLING_OFF)
    module_loader.get_modules.cache_clear()
    org = _org()
    checker = require_feature("anything")

    result = await checker((org, _membership(org.id)), session, redis)

    assert result is None


async def test_require_feature_with_minimum_is_noop_when_billing_disabled(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession, redis: Redis
) -> None:
    monkeypatch.setattr(product_config, "MODULES", _BILLING_OFF)
    module_loader.get_modules.cache_clear()
    org = _org()
    checker = require_feature("anything", minimum=1000)

    result = await checker((org, _membership(org.id)), session, redis)

    assert result is None


# --- T-120: require_feature delegates to FeatureFlagService ---------------------------
# An OrgFeatureFlag override needs a real Organization row to satisfy its FK
# (unlike the tests above, which never persist `_org()`'s in-memory object --
# a plain SELECT against org_feature_flag needs no such row).


async def _persisted_org(session: AsyncSession) -> Organization:
    org = _org()
    session.add(org)
    await session.flush()
    return org


async def test_require_feature_denied_by_override_even_when_plan_allows(
    session: AsyncSession, redis: Redis
) -> None:
    org = await _persisted_org(session)
    plan = await _make_plan(session, features={"seats": 5})
    await _make_subscription(
        session,
        org_id=org.id,
        plan_id=plan.id,
        status="active",
        current_period_end=utcnow() + timedelta(days=20),
    )
    await OrgFeatureFlagRepository(session, org.id).set_override("seats", enabled=False)
    checker = require_feature("seats")

    with pytest.raises(PlanLimitError) as exc_info:
        await checker((org, _membership(org.id)), session, redis)
    assert exc_info.value.status_code == 402


async def test_require_feature_override_cannot_grant_a_flag_the_plan_denies(
    session: AsyncSession, redis: Redis
) -> None:
    org = await _persisted_org(session)
    plan = await _make_plan(session, features={})
    await _make_subscription(
        session,
        org_id=org.id,
        plan_id=plan.id,
        status="active",
        current_period_end=utcnow() + timedelta(days=20),
    )
    await OrgFeatureFlagRepository(session, org.id).set_override("advanced_reports", enabled=True)
    checker = require_feature("advanced_reports")

    with pytest.raises(PlanLimitError) as exc_info:
        await checker((org, _membership(org.id)), session, redis)
    assert exc_info.value.status_code == 402


# --- record_usage (T-132) ------------------------------------------------------
#
# Until now this dependency had no tests and no call sites: usage metering ships
# off by default (`usage_metering` is `enabled=False` in EnabledModules), so
# nothing in the template mounts it. It is still a public API a product is meant
# to attach to its own metered routes, which makes "never exercised" the wrong
# state for it to be in -- these lock the contract that a product would rely on.
#
# Called directly rather than through FastAPI, same as require_feature above.


async def _usage_rows(session: AsyncSession, org_id: UUID) -> list[UsageRecord]:
    """UsageRecordRepository has no list-by-org method (nothing in the app
    needs one), so this reads the table directly -- a test asserting "a row
    landed for this org and not that one" should look at the rows."""
    result = await session.execute(
        select(UsageRecord).where(UsageRecord.org_id == org_id)  # type: ignore[arg-type]
    )
    return list(result.scalars().all())


async def test_record_usage_writes_a_row_for_the_callers_org(
    session: AsyncSession,
) -> None:
    org = _org()
    session.add(org)
    await session.flush()

    recorder = record_usage("api_calls", quantity=3)
    await recorder((org, _membership(org.id)), session)

    records = await _usage_rows(session, org.id)
    assert [(r.metric, r.quantity) for r in records] == [("api_calls", 3)]


async def test_record_usage_defaults_to_one(session: AsyncSession) -> None:
    org = _org()
    session.add(org)
    await session.flush()

    await record_usage("exports")((org, _membership(org.id)), session)

    records = await _usage_rows(session, org.id)
    assert records[0].quantity == 1


async def test_record_usage_never_rejects_the_request(session: AsyncSession) -> None:
    """The difference between this and require_feature, and the reason it is a
    separate dependency: metering observes, it does not gate. A route carrying
    both must fail on the gate, never on the meter."""
    org = _org()
    session.add(org)
    await session.flush()

    # No subscription, no plan, no entitlement -- and still no exception.
    assert await record_usage("api_calls")((org, _membership(org.id)), session) is None


async def test_record_usage_records_against_the_org_it_was_given(
    session: AsyncSession,
) -> None:
    """§0.2, structurally: UsageService is constructed with the org resolved
    from get_current_org, so a metered route can never write usage against a
    org the caller isn't a member of."""
    org_a, org_b = _org(), _org()
    session.add_all([org_a, org_b])
    await session.flush()

    await record_usage("api_calls")((org_a, _membership(org_a.id)), session)

    assert len(await _usage_rows(session, org_a.id)) == 1
    assert await _usage_rows(session, org_b.id) == []


async def test_record_usage_is_noop_when_billing_disabled(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same short-circuit as require_feature, for the same reason: with billing
    off the usage_record table does not exist at all (T-062), so this must
    return before UsageService touches it -- reaching the query would be a
    ProgrammingError, not a no-op."""
    # _BILLING_OFF, not a bare billing=disabled: notifications and
    # file_storage both `requires=["billing"]`, so validate_modules rejects
    # the half-off config at import time.
    monkeypatch.setattr(product_config, "MODULES", _BILLING_OFF)
    module_loader.get_modules.cache_clear()
    org = _org()
    session.add(org)
    await session.flush()

    await record_usage("api_calls")((org, _membership(org.id)), session)

    assert await _usage_rows(session, org.id) == []


async def test_usage_service_dedups_on_an_idempotency_key(session: AsyncSession) -> None:
    """Not reachable through `record_usage` (which passes no key), but it is the
    behaviour a metered route retrying a request depends on -- a second insert
    would otherwise hit UsageRecord.idempotency_key's unique constraint and
    raise instead of returning the row the caller already has."""
    org = _org()
    session.add(org)
    await session.flush()
    service = UsageService(session, org.id)

    first = await service.record(metric="api_calls", quantity=1, idempotency_key="req-1")
    second = await service.record(metric="api_calls", quantity=9, idempotency_key="req-1")

    assert second.id == first.id
    assert second.quantity == 1  # the retry did not double-count
    assert len(await _usage_rows(session, org.id)) == 1
