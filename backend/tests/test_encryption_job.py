from collections.abc import AsyncGenerator
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Column, MetaData, Table, Text, Uuid, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.postgres import PostgresContainer

from app.core import encrypted_type
from app.core.config import get_settings
from app.workers.jobs import encryption as encryption_module
from app.workers.jobs.encryption import _validated_identifier, reencrypt_column_job

metadata = MetaData()
_secrets_table = Table(
    "encryption_job_demo",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("secret_value", Text, nullable=True),
)


@pytest.fixture(scope="module")
def postgres_container() -> "PostgresContainer":
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


# Function-scoped (not module-scoped): pytest-asyncio gives each test its own
# event loop by default, and an asyncpg-backed engine can't be reused across
# loops — see tests/test_db.py for the same pattern.
@pytest.fixture
async def test_engine(postgres_container: "PostgresContainer") -> AsyncGenerator[AsyncEngine, None]:
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
    monkeypatch.setattr(encryption_module, "async_session_factory", factory)


@pytest.fixture
async def session(test_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s


async def _seed_row(session: AsyncSession, value: str | None) -> UUID:
    row_id = uuid4()
    await session.execute(_secrets_table.insert().values(id=row_id, secret_value=value))
    await session.commit()
    return row_id


async def _read_value(session: AsyncSession, row_id: UUID) -> str | None:
    result = await session.execute(
        select(_secrets_table.c.secret_value).where(_secrets_table.c.id == row_id)
    )
    return result.scalar_one()


def test_validated_identifier_accepts_safe_names() -> None:
    assert _validated_identifier("my_table", "table") == "my_table"


def test_validated_identifier_rejects_unsafe_names() -> None:
    with pytest.raises(ValueError, match="Invalid table name"):
        _validated_identifier("bad; DROP TABLE x;", "table")


async def test_reencrypt_column_job_rejects_invalid_table_name() -> None:
    with pytest.raises(ValueError, match="Invalid table name"):
        await reencrypt_column_job({}, table="bad name", column="secret_value")


async def test_reencrypt_column_job_migrates_old_key_and_legacy_rows_idempotently(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    key_a = Fernet.generate_key().decode()
    key_b = Fernet.generate_key().decode()
    fernet_a = Fernet(key_a.encode("ascii"))
    fernet_b = Fernet(key_b.encode("ascii"))

    old_key_ciphertext = fernet_a.encrypt(b"alice-secret").decode("ascii")
    current_key_ciphertext = fernet_b.encrypt(b"bob-secret").decode("ascii")
    legacy_plaintext = "legacy-plaintext-value"

    old_row_id = await _seed_row(session, old_key_ciphertext)
    current_row_id = await _seed_row(session, current_key_ciphertext)
    legacy_row_id = await _seed_row(session, legacy_plaintext)
    null_row_id = await _seed_row(session, None)

    monkeypatch.setenv("ENCRYPTION_KEY", key_b)
    monkeypatch.setenv("ENCRYPTION_KEY_OLD", key_a)
    get_settings.cache_clear()
    encrypted_type._fernet_instance = None

    # batch_size=1 with 4 seeded rows forces multiple batch iterations.
    await reencrypt_column_job({}, table="encryption_job_demo", column="secret_value", batch_size=1)

    rotated_old = await _read_value(session, old_row_id)
    rotated_legacy = await _read_value(session, legacy_row_id)
    unchanged_current = await _read_value(session, current_row_id)
    still_null = await _read_value(session, null_row_id)

    assert rotated_old is not None
    assert fernet_b.decrypt(rotated_old.encode("ascii")) == b"alice-secret"
    assert rotated_legacy is not None
    assert fernet_b.decrypt(rotated_legacy.encode("ascii")) == legacy_plaintext.encode("utf-8")
    assert unchanged_current == current_key_ciphertext  # idempotent skip — untouched
    assert still_null is None

    # Re-running the job is a no-op: every row is already on the primary key,
    # so a second pass must leave them byte-for-byte unchanged.
    await reencrypt_column_job({}, table="encryption_job_demo", column="secret_value", batch_size=1)
    assert await _read_value(session, old_row_id) == rotated_old
    assert await _read_value(session, legacy_row_id) == rotated_legacy
    assert await _read_value(session, current_row_id) == current_key_ciphertext
