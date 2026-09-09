"""
Verifies T-110 (StoredFile table + migration):
  - the real stored_files revision applies cleanly up -> down -> up against a
    real Postgres (mirrors tests/test_notifications_models.py's approach of
    exercising the actual generated revision file, not a throwaway model).
  - `key` is globally unique, including across two different orgs -- the spec
    requires org_id-prefixed keys make collisions impossible in practice, but
    the database must still enforce it.
  - the composite indexes the model declares actually exist in the database,
    not just on the SQLModel class.

Each test gets its own Postgres container so the three concerns stay
independent and neither leaks alembic_version state into the other.
"""

from collections.abc import AsyncGenerator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
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
from app.modules.storage.models import StoredFile
from app.modules.tenancy.models import Organization

# T-291: this file needs file_storage enabled -- its routes, its tables, or
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


def test_stored_files_migration_round_trip(alembic_config: Config) -> None:
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


async def test_stored_file_key_unique_constraint_enforced_across_orgs(
    migrated_session: AsyncSession,
) -> None:
    user = User(email="alice@example.com")
    org_a = Organization(name="Org A", slug="org-a")
    org_b = Organization(name="Org B", slug="org-b")
    migrated_session.add_all([user, org_a, org_b])
    await migrated_session.flush()

    migrated_session.add(
        StoredFile(
            org_id=org_a.id,
            uploaded_by=user.id,
            key="shared-key",
            filename="a.png",
            content_type="image/png",
            size_bytes=1024,
        )
    )
    await migrated_session.flush()

    migrated_session.add(
        StoredFile(
            org_id=org_b.id,
            uploaded_by=user.id,
            key="shared-key",
            filename="b.png",
            content_type="image/png",
            size_bytes=2048,
        )
    )
    with pytest.raises(IntegrityError):
        await migrated_session.flush()


async def test_composite_indexes_present(migrated_session: AsyncSession) -> None:
    result = await migrated_session.execute(
        text("SELECT indexname FROM pg_indexes WHERE tablename = 'stored_file'")
    )
    index_names = {row[0] for row in result.fetchall()}
    assert "ix_stored_file_org_id_status_created_at_id" in index_names
    assert "ix_stored_file_org_id_uploaded_by" in index_names
