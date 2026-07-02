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
"""

from collections.abc import AsyncGenerator, Generator
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
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
from app.modules.billing.dependencies import get_current_org_subscription, require_feature
from app.modules.billing.models import Plan, Subscription
from app.modules.tenancy.models import Membership, Organization
from product import config as product_config

_TABLES = [Plan.__table__, Subscription.__table__]


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


async def test_no_subscription_resolves_to_free_plan(session: AsyncSession) -> None:
    org = _org()

    org_sub = await get_current_org_subscription(
        org_and_membership=(org, _membership(org.id)), session=session
    )

    assert org_sub.subscription is None
    assert org_sub.plan.slug == "free"
    assert org_sub.plan.features == {}


async def test_active_subscription_resolves_to_its_plan(session: AsyncSession) -> None:
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
        org_and_membership=(org, _membership(org.id)), session=session
    )

    assert org_sub.plan.id == plan.id


async def test_trialing_subscription_is_entitled(session: AsyncSession) -> None:
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
        org_and_membership=(org, _membership(org.id)), session=session
    )

    assert org_sub.plan.id == plan.id


async def test_past_due_within_grace_period_is_entitled(session: AsyncSession) -> None:
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
        org_and_membership=(org, _membership(org.id)), session=session
    )

    assert org_sub.plan.id == plan.id


async def test_past_due_beyond_grace_period_falls_back_to_free_plan(
    session: AsyncSession,
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
        org_and_membership=(org, _membership(org.id)), session=session
    )

    assert org_sub.plan.slug == "free"


async def test_canceled_subscription_falls_back_to_free_plan(session: AsyncSession) -> None:
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
        org_and_membership=(org, _membership(org.id)), session=session
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


async def test_require_feature_denies_free_plan(session: AsyncSession) -> None:
    org = _org()
    checker = require_feature("advanced_reports")

    with pytest.raises(PlanLimitError) as exc_info:
        await checker((org, _membership(org.id)), session)
    assert exc_info.value.status_code == 402


async def test_require_feature_allows_when_plan_has_feature(session: AsyncSession) -> None:
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

    result = await checker((org, _membership(org.id)), session)

    assert result is not None
    assert result.plan.id == plan.id


async def test_require_feature_below_minimum_raises(session: AsyncSession) -> None:
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
        await checker((org, _membership(org.id)), session)
    assert exc_info.value.status_code == 402


async def test_require_feature_meets_minimum_allows(session: AsyncSession) -> None:
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

    result = await checker((org, _membership(org.id)), session)

    assert result is not None
    assert result.plan.id == plan.id


async def test_require_feature_is_noop_when_billing_disabled(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession
) -> None:
    monkeypatch.setattr(product_config, "MODULES", _BILLING_OFF)
    module_loader.get_modules.cache_clear()
    org = _org()
    checker = require_feature("anything")

    result = await checker((org, _membership(org.id)), session)

    assert result is None


async def test_require_feature_with_minimum_is_noop_when_billing_disabled(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession
) -> None:
    monkeypatch.setattr(product_config, "MODULES", _BILLING_OFF)
    module_loader.get_modules.cache_clear()
    org = _org()
    checker = require_feature("anything", minimum=1000)

    result = await checker((org, _membership(org.id)), session)

    assert result is None
