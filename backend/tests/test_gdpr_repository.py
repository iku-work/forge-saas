"""
Verifies T-032/T-033's GDPR repositories: ConsentRepository has no way to
mutate or remove a row once written (§0.3 immutability -- granting/revoking
consent is always a new row), create() persists every field, and
list_for_user() is scoped to one user and ordered newest-first.
DataExportJobRepository.create() defaults a fresh job to "pending" and
get_by_id() is a plain lookup (ownership is enforced by GDPRService, not the
repository -- see test_gdpr_service.py). Uses the same real-Postgres
testcontainers pattern as test_audit_repository.py.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel
from testcontainers.postgres import PostgresContainer

from app.modules.auth.models import User
from app.modules.gdpr.models import ConsentRecord, DataExportJob
from app.modules.gdpr.repository import ConsentRepository, DataExportJobRepository

_TABLES = [User.__table__, ConsentRecord.__table__, DataExportJob.__table__]


@pytest.fixture(scope="module")
def postgres_container() -> "PostgresContainer":
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


# Function-scoped (not module-scoped): see test_audit_repository.py -- an
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
def repo(session: AsyncSession) -> ConsentRepository:
    return ConsentRepository(session)


@pytest.fixture
def job_repo(session: AsyncSession) -> DataExportJobRepository:
    return DataExportJobRepository(session)


async def _seed_user(session: AsyncSession) -> User:
    user = User(email=f"{uuid4()}@example.com")
    session.add(user)
    await session.flush()
    return user


# --- immutability / API surface ------------------------------------------------


def test_consent_repository_has_no_update_method() -> None:
    assert not hasattr(ConsentRepository, "update")


def test_consent_repository_has_no_delete_method() -> None:
    assert not hasattr(ConsentRepository, "delete")


def test_consent_repository_has_no_soft_delete_method() -> None:
    assert not hasattr(ConsentRepository, "soft_delete")


# --- create -----------------------------------------------------------------


async def test_create_persists_every_field(repo: ConsentRepository, session: AsyncSession) -> None:
    user = await _seed_user(session)

    record = await repo.create(
        user_id=user.id,
        consent_type="marketing",
        granted=True,
        version="2026-06-01",
        ip_address="127.0.0.1",
        user_agent="pytest",
    )

    assert record.user_id == user.id
    assert record.consent_type == "marketing"
    assert record.granted is True
    assert record.version == "2026-06-01"
    assert record.ip_address == "127.0.0.1"
    assert record.user_agent == "pytest"
    assert record.ts is not None


# --- list_for_user ------------------------------------------------------------


async def test_list_for_user_excludes_other_users(
    repo: ConsentRepository, session: AsyncSession
) -> None:
    mine, theirs = await _seed_user(session), await _seed_user(session)
    await repo.create(
        user_id=theirs.id,
        consent_type="marketing",
        granted=True,
        version="v1",
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    mine_record = await repo.create(
        user_id=mine.id,
        consent_type="marketing",
        granted=True,
        version="v1",
        ip_address="127.0.0.1",
        user_agent="pytest",
    )

    records = await repo.list_for_user(mine.id)

    assert [r.id for r in records] == [mine_record.id]


async def test_list_for_user_orders_newest_first_and_keeps_every_grant(
    repo: ConsentRepository, session: AsyncSession
) -> None:
    user = await _seed_user(session)
    base = datetime(2024, 1, 1, tzinfo=UTC)

    older = await repo.create(
        user_id=user.id,
        consent_type="marketing",
        granted=True,
        version="v1",
        ip_address="127.0.0.1",
        user_agent="pytest",
        ts=base,
    )
    # Revoking consent is a brand-new row, not an update to `older` -- this is
    # what makes the consent log append-only (§0.3).
    newer = await repo.create(
        user_id=user.id,
        consent_type="marketing",
        granted=False,
        version="v1",
        ip_address="127.0.0.1",
        user_agent="pytest",
        ts=base + timedelta(days=1),
    )

    records = await repo.list_for_user(user.id)

    assert [r.id for r in records] == [newer.id, older.id]


# --- DataExportJobRepository ---------------------------------------------------


async def test_job_create_defaults_to_pending_with_no_download_url(
    job_repo: DataExportJobRepository, session: AsyncSession
) -> None:
    user = await _seed_user(session)

    job = await job_repo.create(user_id=user.id)

    assert job.user_id == user.id
    assert job.status == "pending"
    assert job.download_url is None
    assert job.expires_at is None


async def test_job_get_by_id_returns_created_job(
    job_repo: DataExportJobRepository, session: AsyncSession
) -> None:
    user = await _seed_user(session)
    created = await job_repo.create(user_id=user.id)

    fetched = await job_repo.get_by_id(created.id)

    assert fetched is not None
    assert fetched.id == created.id


async def test_job_get_by_id_returns_none_for_unknown_id(
    job_repo: DataExportJobRepository,
) -> None:
    assert await job_repo.get_by_id(uuid4()) is None


async def test_expire_ready_past_expiry_expires_only_ready_jobs_past_expiry(
    job_repo: DataExportJobRepository, session: AsyncSession
) -> None:
    user = await _seed_user(session)
    past = datetime.now(UTC) - timedelta(hours=1)
    future = datetime.now(UTC) + timedelta(hours=1)

    expired = await job_repo.create(user_id=user.id)
    expired.status = "ready"
    expired.download_url = "https://example.com/export.zip"
    expired.expires_at = past

    not_yet_expired = await job_repo.create(user_id=user.id)
    not_yet_expired.status = "ready"
    not_yet_expired.download_url = "https://example.com/export2.zip"
    not_yet_expired.expires_at = future

    still_pending = await job_repo.create(user_id=user.id)
    still_pending.expires_at = past
    await session.flush()

    expired_count = await job_repo.expire_ready_past_expiry()

    assert expired_count == 1
    refetched_expired = await job_repo.get_by_id(expired.id)
    assert refetched_expired is not None
    assert refetched_expired.status == "expired"
    assert refetched_expired.download_url is None

    refetched_future = await job_repo.get_by_id(not_yet_expired.id)
    assert refetched_future is not None
    assert refetched_future.status == "ready"

    refetched_pending = await job_repo.get_by_id(still_pending.id)
    assert refetched_pending is not None
    assert refetched_pending.status == "pending"
