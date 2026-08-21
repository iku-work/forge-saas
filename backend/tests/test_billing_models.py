"""
Verifies T-029 (billing tables + migration):
  - the real billing_tables revision applies cleanly up -> down -> up against
    a real Postgres (mirrors tests/test_tenancy_models.py's approach of
    exercising the actual generated revision file, not a throwaway model).
  - the unique constraint on Subscription.org_id (one subscription per org)
    is enforced by the database, not just declared on the model.

Each test gets its own Postgres container so the two concerns stay
independent and neither leaks alembic_version state into the other.
"""

from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.postgres import PostgresContainer

from app.core.config import get_settings
from app.core.migrations import build_config
from app.core.migrations import upgrade as upgrade_migrations
from app.models._mixins import utcnow
from app.modules.billing.models import Plan, Subscription

# T-291: this file needs billing enabled -- its routes, its tables, or
# both. CI's module-matrix job deselects it in the combination that turns
# every optional module off.
pytestmark = pytest.mark.module_dependent


@pytest.fixture
def postgres_container() -> "PostgresContainer":
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture
def alembic_config(
    postgres_container: "PostgresContainer", monkeypatch: pytest.MonkeyPatch
) -> Config:
    url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()

    return build_config()


def test_billing_tables_migration_round_trip(alembic_config: Config) -> None:
    upgrade_migrations(alembic_config)
    command.downgrade(alembic_config, "base")
    upgrade_migrations(alembic_config)


@pytest.fixture
def migrated_db(alembic_config: Config) -> None:
    # Sync, not async: alembic's env.py drives its own asyncio.run() internally
    # and raises if one is already running, so this must execute before
    # pytest-asyncio opens an event loop for the async fixture below.
    upgrade_migrations(alembic_config)


@pytest.fixture
async def migrated_session(
    migrated_db: None,  # noqa: ARG001 -- ordering dependency only, see migrated_db above
    postgres_container: "PostgresContainer",
) -> AsyncGenerator[AsyncSession, None]:
    url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    engine: AsyncEngine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
    await engine.dispose()


async def test_subscription_org_id_unique_constraint_enforced_by_database(
    migrated_session: AsyncSession,
) -> None:
    plan = Plan(
        name="Pro",
        slug="pro",
        stripe_price_id_monthly="price_pro_monthly",
        stripe_price_id_yearly="price_pro_yearly",
        price_monthly=2900,
        price_yearly=29000,
        features={"seats": 5},
    )
    migrated_session.add(plan)
    await migrated_session.flush()

    org_id = uuid4()
    migrated_session.add(
        Subscription(
            org_id=org_id,
            plan_id=plan.id,
            stripe_subscription_id="sub_1",
            status="active",
            current_period_start=utcnow(),
            current_period_end=utcnow(),
        )
    )
    await migrated_session.flush()

    migrated_session.add(
        Subscription(
            org_id=org_id,
            plan_id=plan.id,
            stripe_subscription_id="sub_2",
            status="active",
            current_period_start=utcnow(),
            current_period_end=utcnow(),
        )
    )
    with pytest.raises(IntegrityError):
        await migrated_session.flush()


async def test_plan_usage_prices_micros_defaults_empty_and_round_trips(
    migrated_session: AsyncSession,
) -> None:
    default_plan = Plan(
        name="Free",
        slug="free-usage-prices-default",
        stripe_price_id_monthly="price_free_monthly",
        stripe_price_id_yearly="price_free_yearly",
        price_monthly=0,
        price_yearly=0,
        features={"seats": 1},
    )
    migrated_session.add(default_plan)
    await migrated_session.flush()
    assert default_plan.usage_prices_micros == {}

    priced_plan = Plan(
        name="Pro",
        slug="pro-usage-prices",
        stripe_price_id_monthly="price_pro_monthly",
        stripe_price_id_yearly="price_pro_yearly",
        price_monthly=2900,
        price_yearly=29000,
        features={"seats": 5},
        usage_meter_event_names={"api_calls": "api_calls_meter"},
        usage_prices_micros={"api_calls": 100},  # $0.0001/call
    )
    migrated_session.add(priced_plan)
    await migrated_session.flush()
    await migrated_session.refresh(priced_plan)
    assert priced_plan.usage_prices_micros == {"api_calls": 100}
