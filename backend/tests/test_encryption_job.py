"""
T-245: the production key-rotation job (app.workers.jobs.encryption).

Everything here runs against a real Postgres, because every property the job
claims -- keyset paging, per-batch commits, a checkpoint that survives a
killed worker -- is a property of the transactions it issues, and none of them
would be exercised by a fake session.

The schema under test is the real one: `user` and `totp_secret` (the two
EncryptedString columns the app ships with today) plus `job_record`, all
created from SQLModel.metadata, and one throwaway `encryption_job_demo` table
registered on that same metadata for the duration of this module. The
throwaway table is what carries the bulk-data cases -- seeding 1,500 rows into
`user` would say more about User's constraints than about the job.
"""

import json
import logging
from collections.abc import AsyncGenerator, Generator
from typing import Any
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet, MultiFernet
from prometheus_client import REGISTRY
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    Uuid,
    event,
    func,
    select,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from app.core import encrypted_type
from app.core.config import get_settings
from app.core.encrypted_type import EncryptedString
from app.core.metrics import REENCRYPT_REMAINING_REDIS_KEY
from app.workers.jobs import encryption as encryption_module
from app.workers.jobs.encryption import (
    JOB_TYPE,
    discover_encrypted_columns,
    rotate_encryption_keys_job,
)
from app.workers.jobs.records import JobRecord

DEMO_TABLE = "encryption_job_demo"
DEMO_LABEL = f"{DEMO_TABLE}.secret_value"

# A Text-typed mirror of the demo table, used to seed and read *raw* tokens:
# going through the EncryptedString column would encrypt on write and decrypt
# on read, which is exactly the layer these tests need to see underneath.
_raw_metadata = MetaData()
raw_demo = Table(
    DEMO_TABLE,
    _raw_metadata,
    Column("id", Uuid, primary_key=True),
    Column("secret_value", Text, nullable=True),
)


# --- fixtures -------------------------------------------------------------


@pytest.fixture(scope="module")
def rotation_postgres() -> Generator[PostgresContainer, None, None]:
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture(scope="module", autouse=True)
def demo_table() -> Generator[Table, None, None]:
    # Registered on the *shared* SQLModel.metadata, since discovery is the
    # thing under test -- and removed again at module teardown so no other
    # test file (or Alembic autogenerate check) ever sees a table that has no
    # migration behind it.
    table = Table(
        DEMO_TABLE,
        SQLModel.metadata,
        Column("id", Uuid, primary_key=True),
        Column("secret_value", EncryptedString, nullable=True),
    )
    yield table
    SQLModel.metadata.remove(table)


def _tables_under_test() -> list[Table]:
    """Every table this file has to create: the encrypted ones, plus whatever
    they point at.

    Derived from discovery rather than hardcoded, so a newly encrypted column
    does not silently stop being covered here. The FK closure used to be
    implicit -- the only reference was `totp_secret -> user`, and `user` is
    itself encrypted, so it came along for free. T-320's `webhook_endpoint`
    broke that: it is encrypted, and it references `organization`, which is
    not. Walking the references to a fixed point is the version that does not
    need revisiting the next time.
    """
    names = {target.table for target in discover_encrypted_columns()} | {"job_record"}
    while True:
        referenced = {
            key.column.table.name
            for name in names
            for key in SQLModel.metadata.tables[name].foreign_keys
        }
        if referenced <= names:
            break
        names |= referenced
    # create_all sorts by dependency itself, so the order here is only for a
    # stable, readable list.
    return [SQLModel.metadata.tables[name] for name in sorted(names)]


# Function-scoped: pytest-asyncio gives each test its own event loop, and an
# asyncpg-backed engine can't be reused across loops (same pattern as
# tests/test_db.py).
@pytest.fixture
async def test_engine(rotation_postgres: PostgresContainer) -> AsyncGenerator[AsyncEngine, None]:
    url = rotation_postgres.get_connection_url().replace("psycopg2", "asyncpg")
    engine = create_async_engine(url)
    tables = _tables_under_test()
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all, tables=tables)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all, tables=tables)
    await engine.dispose()


@pytest.fixture(autouse=True)
def _use_test_engine(monkeypatch: pytest.MonkeyPatch, test_engine: AsyncEngine) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(encryption_module, "async_session_factory", factory)


@pytest.fixture
async def session(test_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as db_session:
        yield db_session


@pytest.fixture(autouse=True)
async def redis_client(
    redis_container: RedisContainer, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[Redis, None]:
    # Autouse: _progress_redis() builds its client from REDIS_URL on every run,
    # so every test in this module needs that pointed at a reachable Redis --
    # not just the one that reads the published gauge back.
    url = (
        f"redis://{redis_container.get_container_host_ip()}:"
        f"{redis_container.get_exposed_port(6379)}/0"
    )
    monkeypatch.setenv("REDIS_URL", url)
    get_settings.cache_clear()
    client: Redis = Redis.from_url(url)
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture
def rotated_keys(monkeypatch: pytest.MonkeyPatch) -> tuple[Fernet, Fernet]:
    """Puts the app mid-rotation: key B is primary, key A is the demoted old key."""
    key_a = Fernet.generate_key().decode()
    key_b = Fernet.generate_key().decode()
    monkeypatch.setenv("ENCRYPTION_KEY", key_b)
    monkeypatch.setenv("ENCRYPTION_KEY_OLD", key_a)
    get_settings.cache_clear()
    encrypted_type._fernet_instance = None
    return Fernet(key_a.encode("ascii")), Fernet(key_b.encode("ascii"))


# --- helpers --------------------------------------------------------------


async def _seed_raw(session: AsyncSession, rows: list[tuple[UUID, str | None]]) -> None:
    await session.execute(
        raw_demo.insert(),
        [{"id": row_id, "secret_value": value} for row_id, value in rows],
    )
    await session.commit()


async def _raw_values(session: AsyncSession) -> dict[UUID, str | None]:
    result = await session.execute(select(raw_demo.c.id, raw_demo.c.secret_value))
    return dict(result.all())  # type: ignore[arg-type] # Row is a 2-tuple here


async def _job_records(session: AsyncSession) -> list[JobRecord]:
    stmt = select(JobRecord).order_by(JobRecord.created_at)  # type: ignore[arg-type] # SQLModel columns aren't Mapped[]-typed; see BaseRepository's docstring
    return list((await session.execute(stmt)).scalars().all())


def _column_report(report: dict[str, Any], label: str) -> dict[str, Any]:
    table, column = label.split(".")
    return next(
        entry
        for entry in report["columns"]
        if entry["table"] == table and entry["column"] == column
    )


# --- discovery ------------------------------------------------------------


def test_discovery_finds_every_encrypted_column() -> None:
    discovered = discover_encrypted_columns()

    assert {target.label for target in discovered} == {
        "user.full_name",
        "totp_secret.secret_encrypted",
        # T-320. Both are the *same* signing secret at different ages, and
        # both have to be re-encrypted: a rotation that moved only the current
        # one would silently strand every consumer still verifying with the
        # previous one during its overlap window.
        "webhook_endpoint.secret",
        "webhook_endpoint.secret_previous",
        # T-331. An IdP client secret has to be *presented* to the IdP, so it
        # is encrypted rather than hashed -- and therefore rotates with
        # everything else here.
        "sso_connection.client_secret",
        DEMO_LABEL,
    }
    # ...paged on each table's own PK, which is not `id` everywhere.
    assert {target.label: target.pk for target in discovered}["totp_secret.secret_encrypted"] == (
        "user_id"
    )


def test_discovery_picks_up_a_newly_encrypted_model() -> None:
    late_arrival = Table(
        "encryption_job_late_model",
        SQLModel.metadata,
        Column("id", Uuid, primary_key=True),
        Column("api_token", EncryptedString),
    )
    try:
        labels = {target.label for target in discover_encrypted_columns()}
    finally:
        SQLModel.metadata.remove(late_arrival)

    assert "encryption_job_late_model.api_token" in labels
    assert "encryption_job_late_model.api_token" not in {
        target.label for target in discover_encrypted_columns()
    }


def test_discovery_rejects_a_composite_primary_key() -> None:
    composite = Table(
        "encryption_job_composite_pk",
        SQLModel.metadata,
        Column("tenant", String, primary_key=True),
        Column("seq", Integer, primary_key=True),
        Column("secret", EncryptedString),
    )
    try:
        with pytest.raises(ValueError, match="2-column primary key"):
            discover_encrypted_columns()
    finally:
        SQLModel.metadata.remove(composite)


def test_discovery_rejects_an_encrypted_primary_key() -> None:
    encrypted_pk = Table(
        "encryption_job_encrypted_pk",
        SQLModel.metadata,
        Column("secret", EncryptedString, primary_key=True),
    )
    try:
        with pytest.raises(ValueError, match="both the primary key and encrypted"):
            discover_encrypted_columns()
    finally:
        SQLModel.metadata.remove(encrypted_pk)


# --- rotation -------------------------------------------------------------


async def test_rotation_moves_every_row_onto_the_new_key(
    session: AsyncSession, rotated_keys: tuple[Fernet, Fernet]
) -> None:
    old_key, new_key = rotated_keys
    plaintexts = {uuid4(): f"secret-{index}" for index in range(1500)}
    await _seed_raw(
        session,
        [
            (row_id, old_key.encrypt(value.encode()).decode("ascii"))
            for row_id, value in plaintexts.items()
        ],
    )

    report = await rotate_encryption_keys_job({}, batch_size=500, throttle_seconds=0)

    stored = await _raw_values(session)
    # Decrypted with the new key *alone* -- not the MultiFernet, which would
    # still succeed on a row that never moved off the old key.
    assert {
        row_id: new_key.decrypt(value.encode("ascii")).decode()  # type: ignore[union-attr] # every seeded row is non-null
        for row_id, value in stored.items()
    } == plaintexts

    demo = _column_report(report, DEMO_LABEL)
    assert demo == {"table": DEMO_TABLE, "column": "secret_value", "scanned": 1500, "rotated": 1500}
    assert report["scanned_total"] == 1500
    assert report["rotated_total"] == 1500
    assert report["dry_run"] is False
    assert report["resumed"] is False

    records = await _job_records(session)
    assert [record.status for record in records] == ["done"]
    assert records[0].job_type == JOB_TYPE
    assert records[0].completed_at is not None
    assert records[0].result is not None
    assert records[0].result["done_columns"] == [
        DEMO_LABEL,
        "sso_connection.client_secret",
        "totp_secret.secret_encrypted",
        "user.full_name",
        "webhook_endpoint.secret",
        "webhook_endpoint.secret_previous",
    ]
    assert records[0].result["cursor"] is None


async def test_rotation_is_idempotent_and_skips_current_legacy_and_null_rows(
    session: AsyncSession, rotated_keys: tuple[Fernet, Fernet]
) -> None:
    old_key, new_key = rotated_keys
    on_old_key = uuid4()
    already_current = uuid4()
    legacy_plaintext = uuid4()
    empty = uuid4()
    current_ciphertext = new_key.encrypt(b"bob-secret").decode("ascii")
    await _seed_raw(
        session,
        [
            (on_old_key, old_key.encrypt(b"alice-secret").decode("ascii")),
            (already_current, current_ciphertext),
            (legacy_plaintext, "never-encrypted"),
            (empty, None),
        ],
    )

    first = await rotate_encryption_keys_job({}, batch_size=1, throttle_seconds=0)
    after_first = await _raw_values(session)

    assert new_key.decrypt(after_first[on_old_key].encode("ascii")) == b"alice-secret"  # type: ignore[union-attr] # seeded non-null
    assert new_key.decrypt(after_first[legacy_plaintext].encode("ascii")) == b"never-encrypted"  # type: ignore[union-attr] # seeded non-null
    assert after_first[already_current] == current_ciphertext  # untouched, byte for byte
    assert after_first[empty] is None
    assert _column_report(first, DEMO_LABEL)["rotated"] == 2

    # A second pass has nothing left to do: every non-null row is already on
    # the primary key, so re-running a finished rotation rewrites nothing.
    second = await rotate_encryption_keys_job({}, batch_size=1, throttle_seconds=0)

    assert await _raw_values(session) == after_first
    assert second["rotated_total"] == 0
    assert second["resumed"] is False  # the finished record is never picked up again


async def test_reads_still_succeed_while_a_rotation_is_half_done(
    session: AsyncSession,
    demo_table: Table,
    monkeypatch: pytest.MonkeyPatch,
    rotated_keys: tuple[Fernet, Fernet],
) -> None:
    old_key, _ = rotated_keys
    plaintexts = {uuid4(): f"secret-{index}" for index in range(4)}
    await _seed_raw(
        session,
        [
            (row_id, old_key.encrypt(value.encode()).decode("ascii"))
            for row_id, value in plaintexts.items()
        ],
    )
    _crash_after_first_batch(monkeypatch)

    # Stop the run halfway: two rows on the new key, two still on the old one.
    with pytest.raises(_SimulatedCrashError):
        await rotate_encryption_keys_job({}, batch_size=2, throttle_seconds=0)

    # Read through the real EncryptedString column: MultiFernet holds both
    # keys, so a half-rotated table is transparent to the application.
    result = await session.execute(
        select(demo_table.c.id, demo_table.c.secret_value).order_by(demo_table.c.id)
    )
    assert dict(result.all()) == plaintexts  # type: ignore[arg-type] # Row is a 2-tuple here


# --- checkpointing / resume ----------------------------------------------


class _SimulatedCrashError(RuntimeError):
    """Stands in for the SIGKILL a redeploy sends mid-run."""


_REAL_THROTTLE = encryption_module._throttle


def _crash_after_first_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(_seconds: float) -> None:
        raise _SimulatedCrashError

    monkeypatch.setattr(encryption_module, "_throttle", _boom)


def _stop_crashing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Not monkeypatch.undo(): that would also roll back the autouse
    # _use_test_engine patch and point the next run at the real DATABASE_URL.
    monkeypatch.setattr(encryption_module, "_throttle", _REAL_THROTTLE)


async def test_resume_continues_from_the_checkpoint(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, rotated_keys: tuple[Fernet, Fernet]
) -> None:
    old_key, new_key = rotated_keys
    plaintexts = {uuid4(): f"secret-{index}" for index in range(1000)}
    await _seed_raw(
        session,
        [
            (row_id, old_key.encrypt(value.encode()).decode("ascii"))
            for row_id, value in plaintexts.items()
        ],
    )

    # Run 1: dies in the inter-batch throttle, after batch 1 committed.
    _crash_after_first_batch(monkeypatch)
    with pytest.raises(_SimulatedCrashError):
        await rotate_encryption_keys_job({}, batch_size=500, throttle_seconds=0.01)

    crashed = (await _job_records(session))[0]
    await session.refresh(crashed)
    assert crashed.status == "running"  # no terminal status: this run never finished
    assert crashed.attempts == 1
    assert crashed.result is not None
    assert crashed.result["scanned"] == {DEMO_LABEL: 500}
    assert crashed.result["cursor"]["table"] == DEMO_TABLE
    assert crashed.result["done_columns"] == []
    rotated_after_crash = sum(
        1 for value in (await _raw_values(session)).values() if _decrypts_under(new_key, value)
    )
    assert rotated_after_crash == 500

    # Run 2: same worker command, no arguments telling it where to pick up.
    _stop_crashing(monkeypatch)
    considered: list[str | None] = []
    original = encryption_module._reencrypted_value

    def _counting(raw_value: str | None, primary: Fernet, all_keys: MultiFernet) -> str | None:
        considered.append(raw_value)
        return original(raw_value, primary, all_keys)

    monkeypatch.setattr(encryption_module, "_reencrypted_value", _counting)

    report = await rotate_encryption_keys_job({}, batch_size=500, throttle_seconds=0)

    assert len(considered) == 500, "batch 1 was re-processed instead of resumed past"
    assert report["resumed"] is True
    assert _column_report(report, DEMO_LABEL)["scanned"] == 1000  # cumulative across both runs
    assert {
        row_id: new_key.decrypt(value.encode("ascii")).decode()  # type: ignore[union-attr] # seeded non-null
        for row_id, value in (await _raw_values(session)).items()
    } == plaintexts

    records = await _job_records(session)
    assert len(records) == 1, "a resumed run must continue the record, not open a second one"
    await session.refresh(records[0])
    assert records[0].status == "done"
    assert records[0].attempts == 2


def _decrypts_under(key: Fernet, value: str | None) -> bool:
    if value is None:
        return False
    try:
        key.decrypt(value.encode("ascii"))
    except Exception:  # noqa: BLE001 -- a test predicate: any failure means "not this key"
        return False
    return True


async def test_resume_false_starts_a_fresh_record(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, rotated_keys: tuple[Fernet, Fernet]
) -> None:
    old_key, _ = rotated_keys
    await _seed_raw(
        session,
        [(uuid4(), old_key.encrypt(b"x").decode("ascii")) for _ in range(4)],
    )

    _crash_after_first_batch(monkeypatch)
    with pytest.raises(_SimulatedCrashError):
        await rotate_encryption_keys_job({}, batch_size=2, throttle_seconds=0)
    _stop_crashing(monkeypatch)

    report = await rotate_encryption_keys_job({}, batch_size=2, throttle_seconds=0, resume=False)

    assert report["resumed"] is False
    records = await _job_records(session)
    assert [record.status for record in records] == ["running", "done"]
    # The abandoned run's rows were still on the new key, so the fresh record
    # re-scanned them and found nothing to do for those.
    assert _column_report(report, DEMO_LABEL)["scanned"] == 4
    assert _column_report(report, DEMO_LABEL)["rotated"] == 2


# --- dry run --------------------------------------------------------------


async def test_dry_run_writes_nothing(
    session: AsyncSession, test_engine: AsyncEngine, rotated_keys: tuple[Fernet, Fernet]
) -> None:
    old_key, new_key = rotated_keys
    on_old_key = [uuid4() for _ in range(3)]
    already_current = uuid4()
    await _seed_raw(
        session,
        [(row_id, old_key.encrypt(b"pending").decode("ascii")) for row_id in on_old_key]
        + [(already_current, new_key.encrypt(b"done").decode("ascii"))],
    )
    before = await _raw_values(session)

    statements: list[str] = []

    @event.listens_for(test_engine.sync_engine, "before_cursor_execute")
    def _record_statement(
        _conn: object, _cursor: object, statement: str, *_args: object, **_kwargs: object
    ) -> None:
        statements.append(statement.strip().split()[0].upper())

    try:
        report = await rotate_encryption_keys_job(
            {}, dry_run=True, batch_size=2, throttle_seconds=0
        )
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", _record_statement)

    assert "SELECT" in statements
    assert not [verb for verb in statements if verb in {"INSERT", "UPDATE", "DELETE"}], statements
    assert await _raw_values(session) == before
    assert await _job_records(session) == []  # not even a JobRecord is written
    assert (await session.execute(select(func.count()).select_from(JobRecord))).scalar_one() == 0

    demo = _column_report(report, DEMO_LABEL)
    assert demo["scanned"] == 4
    assert demo["rotated"] == 3  # the work estimate: rows that *would* be rewritten
    assert report["dry_run"] is True
    assert report["job_record_id"] is None


# --- knobs ----------------------------------------------------------------


async def test_only_restricts_the_run_to_named_columns(
    session: AsyncSession, rotated_keys: tuple[Fernet, Fernet]
) -> None:
    old_key, _ = rotated_keys
    await _seed_raw(session, [(uuid4(), old_key.encrypt(b"x").decode("ascii"))])

    report = await rotate_encryption_keys_job(
        {}, only=[DEMO_LABEL], batch_size=10, throttle_seconds=0
    )

    assert [entry["table"] for entry in report["columns"]] == [DEMO_TABLE]
    records = await _job_records(session)
    assert records[0].payload["columns"] == [DEMO_LABEL]


async def test_cli_passes_its_flags_through_to_the_job(
    session: AsyncSession, capsys: pytest.CaptureFixture[str], rotated_keys: tuple[Fernet, Fernet]
) -> None:
    old_key, _ = rotated_keys
    await _seed_raw(session, [(uuid4(), old_key.encrypt(b"x").decode("ascii")) for _ in range(3)])

    report = await encryption_module._main(
        ["--dry-run", "--batch-size", "2", "--throttle", "0", "--only", DEMO_LABEL, "--no-resume"]
    )

    assert report["dry_run"] is True
    assert _column_report(report, DEMO_LABEL) == {
        "table": DEMO_TABLE,
        "column": "secret_value",
        "scanned": 3,
        "rotated": 3,
    }
    # The operator reads this: the report has to reach stdout, not just the log.
    assert json.loads(capsys.readouterr().out) == report
    assert await _job_records(session) == []


async def test_only_rejects_an_unknown_column() -> None:
    with pytest.raises(ValueError, match="No such encrypted column"):
        await rotate_encryption_keys_job({}, only=["user.nope"])


async def test_batch_size_must_be_positive() -> None:
    with pytest.raises(ValueError, match="batch_size must be at least 1"):
        await rotate_encryption_keys_job({}, batch_size=0)


async def test_throttle_defaults_come_from_settings(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, rotated_keys: tuple[Fernet, Fernet]
) -> None:
    old_key, _ = rotated_keys
    await _seed_raw(session, [(uuid4(), old_key.encrypt(b"x").decode("ascii")) for _ in range(2)])
    monkeypatch.setenv("ENCRYPTION_ROTATION_BATCH_SIZE", "1")
    monkeypatch.setenv("ENCRYPTION_ROTATION_THROTTLE_SECONDS", "0.25")
    get_settings.cache_clear()

    slept: list[float] = []

    async def _record_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(encryption_module, "_throttle", _record_sleep)

    report = await rotate_encryption_keys_job({})

    assert slept == [0.25, 0.25]  # one pause per full page; the empty page that ends it doesn't
    assert _column_report(report, DEMO_LABEL)["scanned"] == 2
    records = await _job_records(session)
    assert records[0].payload == {
        "batch_size": 1,
        "throttle_seconds": 0.25,
        "columns": [
            DEMO_LABEL,
            "sso_connection.client_secret",
            "totp_secret.secret_encrypted",
            "user.full_name",
            "webhook_endpoint.secret",
            "webhook_endpoint.secret_previous",
        ],
    }


async def test_throttle_sleeps_only_for_a_positive_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(encryption_module.asyncio, "sleep", _fake_sleep)

    await encryption_module._throttle(0)
    assert slept == []

    await encryption_module._throttle(0.5)
    assert slept == [0.5]


# --- metrics --------------------------------------------------------------


async def test_metrics_track_rotated_rows_and_publish_the_remaining_gauge(
    session: AsyncSession, redis_client: Redis, rotated_keys: tuple[Fernet, Fernet]
) -> None:
    old_key, _ = rotated_keys
    await _seed_raw(session, [(uuid4(), old_key.encrypt(b"x").decode("ascii")) for _ in range(3)])
    labels = {"table": DEMO_TABLE, "column": "secret_value"}
    before = REGISTRY.get_sample_value("forge_reencrypt_rows_total", labels) or 0.0

    await rotate_encryption_keys_job({}, batch_size=2, throttle_seconds=0)

    after = REGISTRY.get_sample_value("forge_reencrypt_rows_total", labels)
    assert after is not None
    assert after - before == 3
    assert REGISTRY.get_sample_value("forge_reencrypt_batch_seconds_count", labels) is not None
    # The countdown reaches the backend's /metrics through Redis, not through
    # this process's registry -- the job doesn't serve /metrics. Zero once the
    # run is done, so a stale reading can't look like a rotation still going.
    assert await redis_client.get(REENCRYPT_REMAINING_REDIS_KEY) == b"0"


async def test_a_dead_redis_does_not_stop_the_rotation(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    rotated_keys: tuple[Fernet, Fernet],
) -> None:
    old_key, new_key = rotated_keys
    row_id = uuid4()
    await _seed_raw(session, [(row_id, old_key.encrypt(b"x").decode("ascii"))])

    class _DeadRedis:
        async def set(self, *_args: object, **_kwargs: object) -> None:
            raise RedisConnectionError("redis is down")

        async def aclose(self) -> None:
            return

    monkeypatch.setattr(encryption_module, "_progress_redis", _DeadRedis)

    with caplog.at_level(logging.WARNING):
        report = await rotate_encryption_keys_job({}, batch_size=10, throttle_seconds=0)

    assert report["rotated_total"] == 1  # the rotation itself is unaffected
    assert new_key.decrypt((await _raw_values(session))[row_id].encode("ascii")) == b"x"  # type: ignore[union-attr] # seeded non-null
    assert "could not publish the remaining-rows gauge" in caplog.text
