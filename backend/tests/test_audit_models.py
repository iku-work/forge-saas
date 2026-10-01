"""
Verifies T-028 (audit tables + migration):
  - the real audit_events revision applies cleanly up -> down -> up against a
    real Postgres (mirrors tests/test_auth_models.py's approach of exercising
    the actual generated revision file, not a throwaway model).
  - org_id is nullable at the database level, not just on the Python model --
    actions with no org context yet (registration, login) must be insertable.
"""

from collections.abc import AsyncGenerator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.core.migrations import build_config
from app.core.migrations import upgrade as upgrade_migrations
from app.modules.audit.models import AuditEvent
from tests._database import DatabaseHandle


@pytest.fixture
def postgres_container(fresh_database: object) -> object:
    """A per-test database on the shared server (see tests/conftest.py).

    These tests migrate an empty schema, so they need a clean database
    each -- which is what this is. It used to be a whole container each.
    """
    return fresh_database


@pytest.fixture
def alembic_config(postgres_container: DatabaseHandle, monkeypatch: pytest.MonkeyPatch) -> Config:
    url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()

    return build_config()


def test_audit_events_migration_round_trip(alembic_config: Config) -> None:
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
    postgres_container: DatabaseHandle,
) -> AsyncGenerator[AsyncSession, None]:
    url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    engine: AsyncEngine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
    await engine.dispose()


async def test_org_id_is_nullable_in_real_schema(migrated_session: AsyncSession) -> None:
    event = AuditEvent(action="user.registered", resource_type="user", org_id=None)
    migrated_session.add(event)
    await migrated_session.flush()

    assert event.id is not None
    assert event.org_id is None
    assert event.ts is not None
