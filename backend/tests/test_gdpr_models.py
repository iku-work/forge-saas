"""
Verifies T-032 (GDPR tables + migration):
  - the real gdpr_tables revision applies cleanly up -> down -> up against a
    real Postgres (mirrors tests/test_billing_models.py's approach of
    exercising the actual generated revision file, not a throwaway model).
  - ConsentRecord.user_id is a real foreign key in the database, not just a
    Python-level annotation -- a consent row can't be attributed to a
    nonexistent user.

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

from app.core.config import get_settings
from app.core.migrations import build_config
from app.core.migrations import upgrade as upgrade_migrations
from app.modules.gdpr.models import ConsentRecord
from tests._database import DatabaseHandle

# T-291: this file needs gdpr enabled -- its routes, its tables, or
# both. CI's module-matrix job deselects it in the combination that turns
# every optional module off.
pytestmark = pytest.mark.module_dependent


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


def test_gdpr_tables_migration_round_trip(alembic_config: Config) -> None:
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


async def test_consent_record_user_id_foreign_key_enforced_by_database(
    migrated_session: AsyncSession,
) -> None:
    migrated_session.add(
        ConsentRecord(
            user_id=uuid4(),  # no matching row in "user"
            consent_type="marketing",
            granted=True,
            version="v1",
            ip_address="127.0.0.1",
            user_agent="pytest",
        )
    )
    with pytest.raises(IntegrityError):
        await migrated_session.flush()
