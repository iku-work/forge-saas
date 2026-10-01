from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import ClassVar
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import Field, SQLModel

from app.core.exceptions import InvalidCursorError, NotFoundError
from app.core.repository import BaseRepository
from app.models._mixins import SoftDeleteMixin, TimestampMixin, UUIDPKMixin
from tests._database import DatabaseHandle


class TestEntity(UUIDPKMixin, TimestampMixin, SoftDeleteMixin, SQLModel, table=True):
    __tablename__ = "test_entity"
    __test__: ClassVar[bool] = False

    org_id: UUID = Field(index=True)
    name: str


class TestEntityRepository(BaseRepository[TestEntity]):
    __test__: ClassVar[bool] = False
    model = TestEntity


# Function-scoped (not module-scoped): pytest-asyncio gives each test its own
# event loop by default, and an asyncpg-backed engine can't be reused across
# loops — see tests/test_db.py for the same pattern.
@pytest.fixture
async def test_engine(postgres_container: DatabaseHandle) -> AsyncGenerator[AsyncEngine, None]:
    url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all, tables=[TestEntity.__table__])
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all, tables=[TestEntity.__table__])
    await engine.dispose()


@pytest.fixture
async def session(test_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s


@pytest.fixture
def org_a() -> UUID:
    return uuid4()


@pytest.fixture
def org_b() -> UUID:
    return uuid4()


@pytest.fixture
def repo_a(session: AsyncSession, org_a: UUID) -> TestEntityRepository:
    return TestEntityRepository(session, org_a)


@pytest.fixture
def repo_b(session: AsyncSession, org_b: UUID) -> TestEntityRepository:
    return TestEntityRepository(session, org_b)


async def test_get_returns_entity_for_owning_org(repo_a: TestEntityRepository) -> None:
    created = await repo_a.create(name="alice")

    fetched = await repo_a.get(created.id)

    assert fetched is not None
    assert fetched.id == created.id


async def test_get_returns_none_for_other_org(
    repo_a: TestEntityRepository, repo_b: TestEntityRepository
) -> None:
    created = await repo_a.create(name="alice")

    assert await repo_b.get(created.id) is None


async def test_get_excludes_soft_deleted(repo_a: TestEntityRepository) -> None:
    created = await repo_a.create(name="alice")
    await repo_a.soft_delete(created.id)

    assert await repo_a.get(created.id) is None


async def test_get_or_raise_raises_not_found_for_missing(repo_a: TestEntityRepository) -> None:
    with pytest.raises(NotFoundError):
        await repo_a.get_or_raise(uuid4())


async def test_get_or_raise_raises_not_found_across_tenants(
    repo_a: TestEntityRepository, repo_b: TestEntityRepository
) -> None:
    created = await repo_a.create(name="alice")

    with pytest.raises(NotFoundError):
        await repo_b.get_or_raise(created.id)


async def test_create_auto_injects_org_id(repo_a: TestEntityRepository, org_a: UUID) -> None:
    created = await repo_a.create(name="alice")

    assert created.org_id == org_a


async def test_create_allows_explicit_matching_org_id(
    repo_a: TestEntityRepository, org_a: UUID
) -> None:
    created = await repo_a.create(name="alice", org_id=org_a)

    assert created.org_id == org_a


async def test_create_rejects_mismatched_org_id(repo_a: TestEntityRepository, org_b: UUID) -> None:
    with pytest.raises(ValueError, match="org_id"):
        await repo_a.create(name="alice", org_id=org_b)


async def test_update_modifies_fields_within_org(repo_a: TestEntityRepository) -> None:
    created = await repo_a.create(name="alice")

    updated = await repo_a.update(created.id, name="bob")

    assert updated.name == "bob"


async def test_update_raises_not_found_across_tenants(
    repo_a: TestEntityRepository, repo_b: TestEntityRepository
) -> None:
    created = await repo_a.create(name="alice")

    with pytest.raises(NotFoundError):
        await repo_b.update(created.id, name="bob")


async def test_update_rejects_mismatched_org_id(repo_a: TestEntityRepository, org_b: UUID) -> None:
    created = await repo_a.create(name="alice")

    with pytest.raises(ValueError, match="org_id"):
        await repo_a.update(created.id, org_id=org_b)


async def test_soft_delete_sets_deleted_at(
    repo_a: TestEntityRepository, session: AsyncSession
) -> None:
    created = await repo_a.create(name="alice")

    await repo_a.soft_delete(created.id)

    row = await session.get(TestEntity, created.id)
    assert row is not None
    assert row.deleted_at is not None


async def test_soft_delete_raises_not_found_across_tenants(
    repo_a: TestEntityRepository, repo_b: TestEntityRepository
) -> None:
    created = await repo_a.create(name="alice")

    with pytest.raises(NotFoundError):
        await repo_b.soft_delete(created.id)


async def _seed(repo: TestEntityRepository, count: int, *, org_id: UUID) -> list[TestEntity]:
    base = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        await repo.create(name=f"entity-{i}", org_id=org_id, created_at=base + timedelta(minutes=i))
        for i in range(count)
    ]


async def test_list_cursor_paginates_without_duplicates_in_order(
    repo_a: TestEntityRepository,
) -> None:
    seeded = await _seed(repo_a, 5, org_id=repo_a.org_id)
    expected_order = [e.id for e in reversed(seeded)]

    page1, cursor1 = await repo_a.list_cursor(limit=2)
    page2, cursor2 = await repo_a.list_cursor(after=cursor1, limit=2)
    page3, cursor3 = await repo_a.list_cursor(after=cursor2, limit=2)

    assert [e.id for e in page1] == expected_order[0:2]
    assert [e.id for e in page2] == expected_order[2:4]
    assert [e.id for e in page3] == expected_order[4:5]
    assert cursor1 == page1[-1].id
    assert cursor2 == page2[-1].id
    assert cursor3 is None

    seen_ids = {e.id for e in page1} | {e.id for e in page2} | {e.id for e in page3}
    assert len(seen_ids) == 5


async def test_list_cursor_excludes_other_org(
    repo_a: TestEntityRepository, repo_b: TestEntityRepository
) -> None:
    await _seed(repo_b, 2, org_id=repo_b.org_id)
    own = await repo_a.create(name="mine")

    page, cursor = await repo_a.list_cursor()

    assert [e.id for e in page] == [own.id]
    assert cursor is None


async def test_list_cursor_excludes_soft_deleted(repo_a: TestEntityRepository) -> None:
    created = await repo_a.create(name="alice")
    await repo_a.soft_delete(created.id)

    page, cursor = await repo_a.list_cursor()

    assert page == []
    assert cursor is None


# --- cursors that cannot be resolved (repo finding 13) --------------------
#
# Every one of these used to fall back to "no cursor", which does not mean
# "no more pages" -- it means page one. An infinite list appends that page
# instead of replacing it, so the user scrolls and sees the first rows
# again, and reads it as the product losing their data.


async def test_a_cursor_from_another_org_is_refused_not_ignored(
    repo_a: TestEntityRepository, repo_b: TestEntityRepository
) -> None:
    """Still unresolvable across tenants -- it just says so now.

    The old behaviour returned org A's first page, which is not a leak but
    is indistinguishable from a successful "continue" to the client.
    """
    other_org_entity = await repo_b.create(name="theirs")
    await repo_a.create(name="mine")

    with pytest.raises(InvalidCursorError):
        await repo_a.list_cursor(after=other_org_entity.id)


async def test_an_unknown_cursor_is_refused(repo_a: TestEntityRepository) -> None:
    await repo_a.create(name="mine")

    with pytest.raises(InvalidCursorError):
        await repo_a.list_cursor(after=uuid4())


async def test_paging_continues_past_an_anchor_that_was_soft_deleted(
    repo_a: TestEntityRepository,
) -> None:
    """The finding itself, at its smallest.

    Someone deletes a row while a user is scrolling past it. The anchor is
    read only for its (created_at, id), so the position is still perfectly
    good -- but `get()` hides soft-deleted rows, so the clause was dropped
    and page two came back as page one.
    """
    created = [await repo_a.create(name=f"e{i}") for i in range(4)]

    page1, cursor1 = await repo_a.list_cursor(limit=2)
    assert cursor1 is not None
    # The row the cursor names disappears between the two requests.
    await repo_a.soft_delete(cursor1)

    page2, _ = await repo_a.list_cursor(after=cursor1, limit=2)

    ids1 = [e.id for e in page1]
    ids2 = [e.id for e in page2]
    assert not set(ids1) & set(ids2), "page two repeated page one"
    # And it is the *right* continuation, not merely a different one: page
    # two is exactly the rows page one did not show. Compared as sets
    # because the four rows are created in the same instant, so `created_at`
    # ties and the real ordering falls to `id DESC` -- which is uuid4 order,
    # not insertion order.
    assert set(ids2) == {e.id for e in created} - set(ids1)


async def test_a_soft_deleted_anchor_is_not_itself_returned(
    repo_a: TestEntityRepository,
) -> None:
    """Resolving the anchor ignores `deleted_at`; the page it anchors does
    not. Those are different queries and only the first one changed."""
    first = await repo_a.create(name="first")
    second = await repo_a.create(name="second")
    await repo_a.soft_delete(second.id)

    page, _ = await repo_a.list_cursor(limit=10)

    assert [e.id for e in page] == [first.id]
