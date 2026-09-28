"""
Verifies T-028's audit repository: AuditRepository has no way to mutate or
remove a row once written (§0.3 immutability), create() persists every field
including a null org_id for events with no org context, and list_cursor()
is org-scoped cursor pagination ordered on (ts DESC, id DESC). Uses the same
real-Postgres testcontainers pattern as test_tenancy_repository.py.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

from app.core.exceptions import InvalidCursorError
from app.modules.audit.models import AuditEvent
from app.modules.audit.repository import AuditRepository
from tests._database import DatabaseHandle

_TABLES = [AuditEvent.__table__]


# Function-scoped (not module-scoped): see test_tenancy_repository.py — an
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
def repo(session: AsyncSession) -> AuditRepository:
    return AuditRepository(session)


# --- immutability / API surface ------------------------------------------------


def test_audit_repository_has_no_update_method() -> None:
    assert not hasattr(AuditRepository, "update")


def test_audit_repository_has_no_soft_delete_method() -> None:
    assert not hasattr(AuditRepository, "soft_delete")


def test_audit_repository_has_no_delete_method() -> None:
    assert not hasattr(AuditRepository, "delete")


# --- create ---------------------------------------------------------------------


async def test_create_persists_every_field(repo: AuditRepository) -> None:
    org_id = uuid4()
    actor_id = uuid4()

    event = await repo.create(
        action="membership.role_changed",
        resource_type="membership",
        resource_id="m-1",
        org_id=org_id,
        actor_id=actor_id,
        diff={"old_role": "member", "new_role": "admin"},
        ip_address="127.0.0.1",
        user_agent="pytest",
    )

    assert event.action == "membership.role_changed"
    assert event.resource_type == "membership"
    assert event.resource_id == "m-1"
    assert event.org_id == org_id
    assert event.actor_id == actor_id
    assert event.diff == {"old_role": "member", "new_role": "admin"}
    assert event.ip_address == "127.0.0.1"
    assert event.user_agent == "pytest"
    assert event.ts is not None


async def test_create_allows_null_org_id_for_org_less_events(repo: AuditRepository) -> None:
    event = await repo.create(action="user.registered", resource_type="user", org_id=None)

    assert event.org_id is None


# --- list_cursor ----------------------------------------------------------------


async def _seed(repo: AuditRepository, count: int, *, org_id: UUID) -> list[AuditEvent]:
    base = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        await repo.create(
            action=f"action-{i}",
            resource_type="x",
            org_id=org_id,
            ts=base + timedelta(minutes=i),
        )
        for i in range(count)
    ]


async def test_list_cursor_excludes_other_org(repo: AuditRepository) -> None:
    org_a, org_b = uuid4(), uuid4()
    await repo.create(action="other-org", resource_type="x", org_id=org_b)
    mine = await repo.create(action="mine", resource_type="x", org_id=org_a)

    page, cursor = await repo.list_cursor(org_a)

    assert [e.id for e in page] == [mine.id]
    assert cursor is None


async def test_list_cursor_paginates_without_duplicates_in_order(repo: AuditRepository) -> None:
    org_id = uuid4()
    seeded = await _seed(repo, 5, org_id=org_id)
    expected_order = [e.id for e in reversed(seeded)]

    page1, cursor1 = await repo.list_cursor(org_id, limit=2)
    page2, cursor2 = await repo.list_cursor(org_id, after=cursor1, limit=2)
    page3, cursor3 = await repo.list_cursor(org_id, after=cursor2, limit=2)

    assert [e.id for e in page1] == expected_order[0:2]
    assert [e.id for e in page2] == expected_order[2:4]
    assert [e.id for e in page3] == expected_order[4:5]
    assert cursor3 is None

    seen_ids = {e.id for e in page1} | {e.id for e in page2} | {e.id for e in page3}
    assert len(seen_ids) == 5


async def test_a_cursor_from_another_org_is_refused(repo: AuditRepository) -> None:
    """Repo finding 13. Still unresolvable across tenants -- it just says so
    instead of answering with org A's first page, which a client reading an
    infinite feed appends as duplicates."""
    org_a, org_b = uuid4(), uuid4()
    other_org_event = await repo.create(action="theirs", resource_type="x", org_id=org_b)
    await repo.create(action="mine", resource_type="x", org_id=org_a)

    with pytest.raises(InvalidCursorError):
        await repo.list_cursor(org_a, after=other_org_event.id)
