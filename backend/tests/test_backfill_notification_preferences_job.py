"""
Verifies T-101.5's backfill_notification_preferences ARQ job: every user
whose notification_preferences is still the column's empty-dict default gets
DEFAULT_PREFERENCES written, a user who already customised (or already has
the defaults) is left untouched, and running the job twice in a row is a
true no-op the second time -- the same idempotency contract
test_gdpr_purge_job.py / test_encryption_job.py exercise for their jobs.

Uses the same real-Postgres testcontainers pattern as those two: only the
table the job actually touches (User) is created, and the job module's
`async_session_factory` is monkeypatched to a session bound to the test engine.
Per test_gdpr_purge_job.py's docstring, the job runs in its own session, so
every assertion after calling it goes through a brand-new `verify_session`
(never the seeding session) -- with expire_on_commit=False, the seeding
session's identity map would otherwise keep serving stale, pre-backfill
copies of rows it created itself.
"""

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

from app.modules.auth.models import User
from app.modules.notifications.preferences import DEFAULT_PREFERENCES
from app.workers.jobs import notifications as notifications_job_module
from app.workers.jobs.notifications import backfill_notification_preferences
from tests._database import DatabaseHandle

_TABLES = [User.__table__]

_CUSTOM_PREFERENCES = {"payment_failed": {"email": False, "in_app": True, "push": True}}


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


@pytest.fixture(autouse=True)
def _use_test_engine(monkeypatch: pytest.MonkeyPatch, test_engine: AsyncEngine) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(notifications_job_module, "async_session_factory", factory)


@pytest.fixture
def session_factory(test_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as s:
        yield s


async def _make_user(session: AsyncSession, email: str, *, preferences: dict | None = None) -> User:
    user = (
        User(email=email)
        if preferences is None
        else User(email=email, notification_preferences=preferences)
    )
    session.add(user)
    await session.commit()
    return user


async def test_backfill_sets_defaults_for_empty_users_only(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    empty_user = await _make_user(session, "empty@example.com")
    customised_user = await _make_user(
        session, "customised@example.com", preferences=_CUSTOM_PREFERENCES
    )

    await backfill_notification_preferences({})

    async with session_factory() as verify_session:
        refreshed_empty = (
            await verify_session.execute(select(User).where(User.id == empty_user.id))
        ).scalar_one()
        refreshed_customised = (
            await verify_session.execute(select(User).where(User.id == customised_user.id))
        ).scalar_one()

        assert refreshed_empty.notification_preferences == DEFAULT_PREFERENCES
        assert refreshed_customised.notification_preferences == _CUSTOM_PREFERENCES


async def test_backfill_is_idempotent(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user = await _make_user(session, "idempotent@example.com")

    await backfill_notification_preferences({})
    async with session_factory() as verify_session:
        first_pass_preferences = (
            (await verify_session.execute(select(User).where(User.id == user.id)))
            .scalar_one()
            .notification_preferences
        )

    await backfill_notification_preferences({})
    async with session_factory() as verify_session:
        second_pass_preferences = (
            (await verify_session.execute(select(User).where(User.id == user.id)))
            .scalar_one()
            .notification_preferences
        )

    assert first_pass_preferences == DEFAULT_PREFERENCES
    assert second_pass_preferences == DEFAULT_PREFERENCES
