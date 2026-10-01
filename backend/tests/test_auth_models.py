"""
Verifies T-013 (auth tables + migration):
  - the real auth_tables revision applies cleanly up -> down -> up against a
    real Postgres (T-010's tests/test_alembic.py already covers the alembic
    *machinery* generically with a throwaway model — this exercises the
    actual generated revision file instead).
  - the (provider, provider_user_id) unique constraint on OAuthAccount is
    enforced by the database, not just declared on the model.

Each test gets its own Postgres container so the two concerns stay
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

from app.core.config import get_settings
from app.core.migrations import build_config
from app.core.migrations import upgrade as upgrade_migrations
from app.modules.auth.models import OAuthAccount, User
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


def test_auth_tables_migration_round_trip(alembic_config: Config) -> None:
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


async def test_oauth_account_unique_constraint_enforced_by_database(
    migrated_session: AsyncSession,
) -> None:
    user = User(email="alice@example.com")
    migrated_session.add(user)
    await migrated_session.flush()

    migrated_session.add(
        OAuthAccount(user_id=user.id, provider="google", provider_user_id="abc123")
    )
    await migrated_session.flush()

    migrated_session.add(
        OAuthAccount(user_id=user.id, provider="google", provider_user_id="abc123")
    )
    with pytest.raises(IntegrityError):
        await migrated_session.flush()


@pytest.mark.owasp("A02")
async def test_full_name_is_encrypted_at_rest(migrated_session: AsyncSession) -> None:
    user = User(email="carol@example.com", full_name="Carol Danvers")
    migrated_session.add(user)
    await migrated_session.flush()

    raw = (
        await migrated_session.execute(
            text('SELECT full_name FROM "user" WHERE id = :id'), {"id": user.id}
        )
    ).scalar_one()
    assert raw.startswith("gAAAAA")
    assert "Carol Danvers" not in raw

    await migrated_session.refresh(user)
    assert user.full_name == "Carol Danvers"
