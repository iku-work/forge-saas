"""
Verifies T-025 (tenancy tables + migration):
  - the real tenancy_tables revision applies cleanly up -> down -> up against
    a real Postgres (mirrors tests/test_auth_models.py's approach of
    exercising the actual generated revision file, not a throwaway model).
  - the (user_id, org_id) unique constraint on Membership is enforced by the
    database, not just declared on the model.

Each test gets its own Postgres container so the two concerns stay
independent and neither leaks alembic_version state into the other.
"""

from collections.abc import AsyncGenerator

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
from app.modules.auth.models import User
from app.modules.auth.permissions import MemberRole
from app.modules.tenancy.models import Membership, Organization


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


def test_tenancy_tables_migration_round_trip(alembic_config: Config) -> None:
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


async def test_membership_unique_constraint_enforced_by_database(
    migrated_session: AsyncSession,
) -> None:
    user = User(email="alice@example.com")
    org = Organization(name="Acme", slug="acme")
    migrated_session.add_all([user, org])
    await migrated_session.flush()

    migrated_session.add(Membership(user_id=user.id, org_id=org.id, role=MemberRole.OWNER))
    await migrated_session.flush()

    migrated_session.add(Membership(user_id=user.id, org_id=org.id, role=MemberRole.MEMBER))
    with pytest.raises(IntegrityError):
        await migrated_session.flush()
