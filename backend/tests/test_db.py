from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core import db as db_module
from app.core.exceptions import PermissionDeniedError
from tests._database import DatabaseHandle

metadata = MetaData()
_demo_table = Table(
    "test_db_demo",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String, nullable=False),
)

# get_session() is a FastAPI-style yield-dependency: only wrapping it as a real
# async context manager (as FastAPI does internally) exercises the rollback path,
# since a plain `async for` over the generator never delivers the exception to it.
session_cm = asynccontextmanager(db_module.get_session)


@pytest.fixture
async def test_engine(postgres_container: DatabaseHandle) -> AsyncGenerator[AsyncEngine, None]:
    url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
    await engine.dispose()


@pytest.fixture(autouse=True)
def _use_test_engine(monkeypatch: pytest.MonkeyPatch, test_engine: AsyncEngine) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(db_module, "write_engine", test_engine)
    monkeypatch.setattr(db_module, "async_session_factory", factory)
    # T-246: both halves, and to the *same* objects -- that is what "no replica
    # configured" means (read_replica_configured() stays False), so nothing
    # here accidentally reaches the real DATABASE_URL through the read side.
    monkeypatch.setattr(db_module, "read_engine", test_engine)
    monkeypatch.setattr(db_module, "read_session_factory", factory)


async def test_get_session_commits_a_row() -> None:
    async with session_cm() as session:
        await session.execute(_demo_table.insert().values(id=1, name="alice"))
        await session.commit()

    async with session_cm() as session:
        row = (await session.execute(select(_demo_table).where(_demo_table.c.id == 1))).first()

    assert row is not None
    assert row.name == "alice"


async def _insert_then_raise(session: AsyncSession) -> None:
    await session.execute(_demo_table.insert().values(id=2, name="bob"))
    raise RuntimeError("boom")


async def test_get_session_rolls_back_on_exception() -> None:
    with pytest.raises(RuntimeError, match="boom"):
        async with session_cm() as session:
            await _insert_then_raise(session)

    async with session_cm() as session:
        row = (await session.execute(select(_demo_table).where(_demo_table.c.id == 2))).first()

    assert row is None


async def test_dispose_engine_runs_without_error() -> None:
    await db_module.dispose_engine()


async def _write_then_reject(session: AsyncSession) -> None:
    await session.execute(_demo_table.insert().values(id=3, name="unrelated"))

    async def _consequence() -> None:
        await session.execute(_demo_table.insert().values(id=4, name="revocation"))

    await db_module.persist_then_reject(
        session, _consequence, PermissionDeniedError("errors.test", fallback="denied")
    )


async def test_persist_then_reject_keeps_the_consequence_and_nothing_else() -> None:
    """Review R2: a rejection whose consequence must survive it.

    Driven through get_session, so the rejection unwinds the same rollback a
    real request's does. Before the request fails it writes something
    unrelated (id=3); the consequence writes the security state (id=4). Only
    the consequence may be left afterwards.
    """
    with pytest.raises(PermissionDeniedError):
        async with session_cm() as session:
            await _write_then_reject(session)

    async with session_cm() as session:
        names = (await session.execute(select(_demo_table.c.name))).scalars().all()

    assert names == ["revocation"]
