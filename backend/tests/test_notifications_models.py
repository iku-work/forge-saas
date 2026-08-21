"""
Verifies T-101 (notifications + push_token tables + migration):
  - the real notifications_tables revision applies cleanly up -> down -> up
    against a real Postgres (mirrors tests/test_billing_models.py's approach
    of exercising the actual generated revision file, not a throwaway model).
  - the unique constraint on PushToken.token is enforced by the database, not
    just declared on the model.
  - two notifications for the same user are both independently queryable.
  - the composite indexes the model declares actually exist in the database,
    not just on the SQLModel class.

Each test gets its own Postgres container so the three concerns stay
independent and neither leaks alembic_version state into the other.
"""

from collections.abc import AsyncGenerator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
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
from app.modules.notifications.models import Notification, PushToken
from app.modules.tenancy.models import Organization

# T-291: this file needs notifications enabled -- its routes, its tables, or
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


def test_notifications_tables_migration_round_trip(alembic_config: Config) -> None:
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


async def test_push_token_unique_constraint_enforced_by_database(
    migrated_session: AsyncSession,
) -> None:
    user = User(email="alice@example.com")
    migrated_session.add(user)
    await migrated_session.flush()

    migrated_session.add(
        PushToken(
            user_id=user.id,
            token="ExponentPushToken[aaa]",
            platform="ios",
            last_used_at=user.created_at,
        )
    )
    await migrated_session.flush()

    migrated_session.add(
        PushToken(
            user_id=user.id,
            token="ExponentPushToken[aaa]",
            platform="android",
            last_used_at=user.created_at,
        )
    )
    with pytest.raises(IntegrityError):
        await migrated_session.flush()


async def test_two_notifications_for_same_user_both_queryable(
    migrated_session: AsyncSession,
) -> None:
    user = User(email="bob@example.com")
    org = Organization(name="Acme", slug="acme")
    migrated_session.add_all([user, org])
    await migrated_session.flush()

    migrated_session.add_all(
        [
            Notification(
                org_id=org.id,
                user_id=user.id,
                type="member_invited",
                title="You were invited",
                body="Join Acme",
                channels=["email", "in_app"],
            ),
            Notification(
                org_id=org.id,
                user_id=user.id,
                type="payment_failed",
                title="Payment failed",
                body="Update your card",
                channels=["email", "in_app", "push"],
            ),
        ]
    )
    await migrated_session.flush()

    result = await migrated_session.execute(
        select(Notification).where(Notification.user_id == user.id)
    )
    rows = result.scalars().all()
    assert {row.type for row in rows} == {"member_invited", "payment_failed"}


async def test_composite_indexes_present(migrated_session: AsyncSession) -> None:
    result = await migrated_session.execute(
        text("SELECT indexname FROM pg_indexes WHERE tablename = 'notification'")
    )
    index_names = {row[0] for row in result.fetchall()}
    assert "ix_notification_user_id_read_at" in index_names
    assert "ix_notification_org_id_user_id_created_at_id" in index_names


async def test_push_token_token_unique_index_present(migrated_session: AsyncSession) -> None:
    result = await migrated_session.execute(
        text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE tablename = 'push_token' AND indexdef ILIKE '%UNIQUE%token%'"
        )
    )
    assert result.fetchone() is not None
