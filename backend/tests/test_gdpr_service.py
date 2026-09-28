"""
Verifies T-033 (GDPR service): requesting an export creates a pending job and
enqueues the (stubbed) export job, get_export_job is ownership-scoped to the
requesting user, delete_me soft-deletes the user and writes an audit row, and
record_consent is append-only -- calling it twice for the same user produces
two rows, never an update to the first. Uses the same real-Postgres
testcontainers pattern as test_tenancy_service.py.
"""

from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

from app.core.exceptions import NotFoundError
from app.modules.audit.models import AuditEvent
from app.modules.auth.models import User
from app.modules.gdpr.models import ConsentRecord, DataExportJob
from app.modules.gdpr.service import GDPRService
from tests._database import DatabaseHandle

_TABLES = [User.__table__, ConsentRecord.__table__, DataExportJob.__table__, AuditEvent.__table__]


# Function-scoped (not module-scoped): see test_auth_repository.py -- an
# asyncpg-backed engine can't be reused across pytest-asyncio's per-test loop.
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


@pytest.fixture
async def session(test_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s


@pytest.fixture
def service(session: AsyncSession) -> GDPRService:
    return GDPRService(session)


@pytest.fixture
async def user(session: AsyncSession) -> User:
    u = User(email=f"user-{uuid4()}@example.com")
    session.add(u)
    await session.flush()
    return u


# --- request_export -----------------------------------------------------------


async def test_request_export_creates_pending_job(
    service: GDPRService, user: User, session: AsyncSession
) -> None:
    job = await service.request_export(user.id)

    assert job.user_id == user.id
    assert job.status == "pending"
    persisted = (
        await session.execute(select(DataExportJob).where(DataExportJob.id == job.id))
    ).scalar_one()
    assert persisted.status == "pending"


async def test_request_export_enqueues_export_job(
    service: GDPRService, user: User, enqueued_jobs: list[tuple[str, dict[str, Any]]]
) -> None:
    job = await service.request_export(user.id)

    function, kwargs = enqueued_jobs[-1]
    assert function == "export_user_data"
    assert kwargs == {"user_id": user.id, "job_id": job.id}


# --- get_export_job -------------------------------------------------------------


async def test_get_export_job_returns_job_for_owner(service: GDPRService, user: User) -> None:
    job = await service.request_export(user.id)

    fetched = await service.get_export_job(user.id, job.id)

    assert fetched.id == job.id


async def test_get_export_job_raises_for_unknown_id(service: GDPRService, user: User) -> None:
    with pytest.raises(NotFoundError):
        await service.get_export_job(user.id, uuid4())


async def test_get_export_job_raises_for_non_owner(
    service: GDPRService, user: User, session: AsyncSession
) -> None:
    job = await service.request_export(user.id)
    other = User(email=f"other-{uuid4()}@example.com")
    session.add(other)
    await session.flush()

    with pytest.raises(NotFoundError):
        await service.get_export_job(other.id, job.id)


# --- delete_me -------------------------------------------------------------------


async def test_delete_me_soft_deletes_user(
    service: GDPRService, user: User, session: AsyncSession
) -> None:
    await service.delete_me(user)

    persisted = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
    assert persisted.deleted_at is not None


async def test_delete_me_writes_audit_event(
    service: GDPRService, user: User, session: AsyncSession
) -> None:
    await service.delete_me(user)

    event = (
        await session.execute(
            select(AuditEvent).where(AuditEvent.action == "user.deletion_requested")
        )
    ).scalar_one()
    assert event.actor_id == user.id
    assert event.resource_id == str(user.id)


async def test_delete_me_enqueues_purge_job(
    service: GDPRService, user: User, enqueued_jobs: list[tuple[str, dict[str, Any]]]
) -> None:
    await service.delete_me(user)

    function, kwargs = enqueued_jobs[-1]
    assert function == "purge_user_data"
    assert kwargs["user_id"] == user.id
    assert "_defer_by" in kwargs


# --- record_consent / list_consents ---------------------------------------------


async def test_record_consent_persists_fields(service: GDPRService, user: User) -> None:
    record = await service.record_consent(
        user_id=user.id,
        consent_type="marketing",
        granted=True,
        version="2026-06-01",
        ip="127.0.0.1",
        ua="pytest",
    )

    assert record.user_id == user.id
    assert record.consent_type == "marketing"
    assert record.granted is True


async def test_record_consent_twice_creates_two_rows(service: GDPRService, user: User) -> None:
    await service.record_consent(
        user_id=user.id,
        consent_type="marketing",
        granted=True,
        version="v1",
        ip="127.0.0.1",
        ua="pytest",
    )
    await service.record_consent(
        user_id=user.id,
        consent_type="marketing",
        granted=False,
        version="v1",
        ip="127.0.0.1",
        ua="pytest",
    )

    records = await service.list_consents(user.id)

    assert len(records) == 2


async def test_list_consents_excludes_other_users(
    service: GDPRService, user: User, session: AsyncSession
) -> None:
    other = User(email=f"other-{uuid4()}@example.com")
    session.add(other)
    await session.flush()
    await service.record_consent(
        user_id=other.id,
        consent_type="marketing",
        granted=True,
        version="v1",
        ip="127.0.0.1",
        ua="pytest",
    )

    records = await service.list_consents(user.id)

    assert records == []
