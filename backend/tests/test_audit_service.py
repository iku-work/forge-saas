"""
Verifies T-028's AuditService.log(): writes happen inside the caller's own
transaction (visible via flush, before any commit) and a write failure is
swallowed -- logged, never raised -- without poisoning the session for
whatever the caller does next. Uses the same real-Postgres testcontainers
pattern as test_audit_repository.py.
"""

from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel
from testcontainers.postgres import PostgresContainer

from app.modules.audit.models import AuditEvent
from app.modules.audit.service import AuditService

_TABLES = [AuditEvent.__table__]


@pytest.fixture(scope="module")
def postgres_container() -> "PostgresContainer":
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


# Function-scoped (not module-scoped): see test_audit_repository.py — an
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
def service(session: AsyncSession) -> AuditService:
    return AuditService(session)


# --- happy path -------------------------------------------------------------


async def test_log_writes_row_visible_before_commit(
    service: AuditService, session: AsyncSession
) -> None:
    org_id = uuid4()
    actor_id = uuid4()

    await service.log(
        action="membership.role_changed",
        resource_type="membership",
        resource_id="m-1",
        org_id=org_id,
        actor_id=actor_id,
        diff={"old_role": "member", "new_role": "admin"},
        ip="127.0.0.1",
        ua="pytest",
    )

    row = (
        await session.execute(text("SELECT action, org_id, actor_id, diff FROM audit_event"))
    ).one()
    assert row.action == "membership.role_changed"
    assert str(row.org_id) == str(org_id)
    assert str(row.actor_id) == str(actor_id)
    assert row.diff == {"old_role": "member", "new_role": "admin"}


async def test_log_allows_null_org_id(service: AuditService, session: AsyncSession) -> None:
    await service.log(action="user.registered", resource_type="user", org_id=None)

    row = (await session.execute(text("SELECT org_id FROM audit_event"))).one()
    assert row.org_id is None


# --- failure swallowing -------------------------------------------------------


async def test_log_swallows_repository_failure_without_raising(
    service: AuditService, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(**_kwargs: Any) -> AuditEvent:  # noqa: ANN401 -- mirrors AuditRepository.create's signature
        raise RuntimeError("boom")

    monkeypatch.setattr(service._repo, "create", _boom)

    await service.log(action="user.registered", resource_type="user", org_id=None)


async def test_log_failure_does_not_poison_session_for_later_use(
    service: AuditService, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(**_kwargs: Any) -> AuditEvent:  # noqa: ANN401 -- mirrors AuditRepository.create's signature
        raise RuntimeError("boom")

    monkeypatch.setattr(service._repo, "create", _boom)
    await service.log(action="user.registered", resource_type="user", org_id=None)

    # The session must still be usable afterward -- the SAVEPOINT rollback
    # inside log() must not have aborted the caller's outer transaction.
    result = await session.execute(text("SELECT 1"))
    assert result.scalar_one() == 1
