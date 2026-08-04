"""
ARQ job (T-245): re-encrypts every EncryptedString column in the schema onto
the current primary ENCRYPTION_KEY, after a rotation has moved the previous
key into ENCRYPTION_KEY_OLD (app.core.encrypted_type's MultiFernet keeps
reading both meanwhile). The operator-facing procedure -- including the
crash-recovery and rollback stories -- is
docs/runbooks/encryption-key-rotation.md.

Operationally this is one long table scan per encrypted column, so the job is
built around four properties:

* **Discovery, not configuration.** The (table, column) set is read from
  SQLModel.metadata at run time, so a column that starts using
  EncryptedString later is picked up without anyone remembering to edit a
  list here.
* **Keyset pagination** on each table's single-column primary key -- §0.3
  forbids OFFSET, which on a multi-million-row table would also re-walk the
  whole prefix on every batch.
* **Checkpointed** into a JobRecord (T-034) row, updated in the *same*
  transaction as the batch it describes: a worker that is killed or
  redeployed mid-run resumes from the last committed batch, and can never
  record progress for rows it didn't actually write.
* **Idempotent** row by row -- a value that already decrypts under the
  primary key is left byte-for-byte alone (see _reencrypted_value), so
  re-running a finished or half-finished rotation is always safe.

Two things this job deliberately does *not* touch. It never reads a row
through the ORM, so no EncryptedString bind/result processing runs (see
_scan_table) -- the whole point is to move raw tokens. And it invalidates no
cache (T-240): cached values are plaintext, and rotation changes only the
ciphertext at rest, so every cached entry stays correct throughout.
"""

import argparse
import asyncio
import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import Column, MetaData, Row, Table, Text, Update, bindparam, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import SQLModel

import app.models  # noqa: F401 -- registers every module's tables on SQLModel.metadata
from app.core.config import get_settings
from app.core.db import async_session_factory, dispose_engine
from app.core.encrypted_type import EncryptedString, get_shared_fernet
from app.core.metrics import (
    FORGE_REENCRYPT_BATCH_SECONDS,
    FORGE_REENCRYPT_ROWS_TOTAL,
    REENCRYPT_REMAINING_REDIS_KEY,
)
from app.core.tracing import trace_job
from app.models._mixins import utcnow
from app.workers.jobs.records import JobRecord

logger = logging.getLogger(__name__)

JOB_TYPE = "rotate_encryption_keys"

_STATUS_RUNNING = "running"
_STATUS_DONE = "done"

_PK_BIND = "keyset_pk"
_VALUE_BIND = "ciphertext"


# ---------------------------------------------------------------------------
# Column discovery
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EncryptedColumn:
    """One (table, column) pair the job has to walk, plus the column it pages on."""

    table: str
    column: str
    pk: str

    @property
    def label(self) -> str:
        return f"{self.table}.{self.column}"


def _keyset_pk(table: Table, column: Column[Any]) -> Column[Any]:
    """The single PK column `table` is paged on, or a clear error saying why it can't be."""
    primary_key_columns = list(table.primary_key.columns)
    if len(primary_key_columns) != 1:
        raise ValueError(
            f"{table.name}.{column.name} is encrypted, but {table.name} has a "
            f"{len(primary_key_columns)}-column primary key and this job pages on a "
            "single one. Give the table a surrogate PK (UUIDPKMixin), or re-encrypt "
            "it out of band."
        )
    primary_key_column = primary_key_columns[0]
    if primary_key_column.name == column.name:
        raise ValueError(
            f"{table.name}.{column.name} is both the primary key and encrypted. "
            "Ciphertext is non-deterministic, so such a column can be neither "
            "looked up nor paged on -- keep the key in plaintext and store the "
            "secret in a second, encrypted column."
        )
    return primary_key_column


def discover_encrypted_columns() -> tuple[EncryptedColumn, ...]:
    """Every EncryptedString column registered on SQLModel.metadata.

    Sorted by (table, column) rather than left in metadata order: the sequence
    is part of the checkpoint contract -- a resumed run must walk the columns
    in the same order as the run it resumes, whatever order the models
    happened to be imported in.
    """
    found = [
        EncryptedColumn(table.name, column.name, _keyset_pk(table, column).name)
        for table in SQLModel.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, EncryptedString)
    ]
    return tuple(sorted(found, key=lambda target: (target.table, target.column)))


def _scan_table(target: EncryptedColumn) -> Table:
    """A detached mirror of `target`'s table holding only (pk, ciphertext column).

    The ciphertext column is typed as plain Text, not EncryptedString: the real
    column's TypeDecorator would decrypt on read and re-encrypt on write, which
    is precisely the layer this job exists to bypass -- it moves tokens, and a
    token no configured key can open (a corrupt row) has to survive the trip
    rather than raise. Building a Table instead of interpolating identifiers
    into text() also leaves quoting to SQLAlchemy, so there is no raw SQL here
    for §0.3 to make an exception for.
    """
    source = SQLModel.metadata.tables[target.table]
    primary_key_column = source.c[target.pk]
    return Table(
        target.table,
        MetaData(),
        Column(primary_key_column.name, primary_key_column.type, primary_key=True),
        Column(target.column, Text),
        schema=source.schema,
    )


def _decode_pk(scan: Table, target: EncryptedColumn, raw: str) -> Any:  # noqa: ANN401 -- the PK's own Python type, whatever the model declared it as
    """JSON round-trips a checkpoint cursor as a string; the WHERE clause needs the real type."""
    return scan.c[target.pk].type.python_type(raw)


# ---------------------------------------------------------------------------
# Per-value rotation
# ---------------------------------------------------------------------------


def _reencrypted_value(raw_value: str | None, primary: Fernet, all_keys: MultiFernet) -> str | None:
    """Returns new primary-key ciphertext for `raw_value`, or None if no change is needed."""
    if raw_value is None:
        return None

    try:
        primary.decrypt(raw_value.encode("ascii"))
    except (InvalidToken, UnicodeEncodeError):
        pass
    else:
        return None  # already encrypted with the primary key — idempotent skip

    try:
        plaintext_bytes = all_keys.decrypt(raw_value.encode("ascii"))
    except (InvalidToken, UnicodeEncodeError):
        # Not decryptable by any known key — either it was never encrypted
        # (legacy plaintext, the pre-encryption migration path) or it's
        # genuinely corrupt. Either way, the safe move is to encrypt the raw
        # bytes as-is: a legacy row gets migrated; corrupt ciphertext is
        # preserved (re-encrypting garbage is no worse than leaving it).
        plaintext_bytes = raw_value.encode("utf-8")

    return primary.encrypt(plaintext_bytes).decode("ascii")


# ---------------------------------------------------------------------------
# Checkpointing
# ---------------------------------------------------------------------------


@dataclass
class _Progress:
    """The resumable state of one rotation, mirrored into JobRecord.result.

    `cursor` is the position *inside* the column currently being walked
    ({"table", "column", "last_pk"}); `done_columns` are the ones already
    finished. Counts are cumulative across every run that has touched the
    record, which is what makes the final report describe the whole rotation
    rather than only its last attempt.
    """

    done_columns: list[str] = field(default_factory=list)
    cursor: dict[str, str] | None = None
    scanned: dict[str, int] = field(default_factory=dict)
    rotated: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_record(cls, record: JobRecord) -> "_Progress":
        raw: dict[str, Any] = record.result or {}
        return cls(
            done_columns=list(raw.get("done_columns", [])),
            cursor=raw.get("cursor"),
            scanned=dict(raw.get("scanned", {})),
            rotated=dict(raw.get("rotated", {})),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "done_columns": list(self.done_columns),
            "cursor": self.cursor,
            "scanned": dict(self.scanned),
            "rotated": dict(self.rotated),
        }

    def resume_point(self, target: EncryptedColumn) -> str | None:
        cursor = self.cursor
        if cursor is None or cursor["table"] != target.table or cursor["column"] != target.column:
            return None
        return cursor["last_pk"]


async def _resumable_record(session: AsyncSession) -> JobRecord | None:
    """The newest rotation still in `running` — i.e. one whose worker died.

    A killed or failing run never gets to write a terminal status, so
    "running" is exactly the set of interrupted runs; a finished one is
    `done` and is never picked up again.
    """
    model = cast("Any", JobRecord)
    stmt = (
        select(JobRecord)
        .where(model.job_type == JOB_TYPE, model.status == _STATUS_RUNNING)
        .order_by(model.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def _open_record(
    session: AsyncSession, *, resume: bool, payload: dict[str, Any]
) -> tuple[JobRecord, bool]:
    existing = await _resumable_record(session) if resume else None
    if existing is not None:
        existing.attempts += 1
        session.add(existing)
        await session.commit()
        return existing, True

    record = JobRecord(
        job_type=JOB_TYPE,
        status=_STATUS_RUNNING,
        payload=payload,
        result=_Progress().as_dict(),
        attempts=1,
        started_at=utcnow(),
    )
    session.add(record)
    await session.commit()
    return record, False


async def _commit_checkpoint(
    session: AsyncSession, record: JobRecord | None, progress: _Progress
) -> None:
    """Persists the batch and the checkpoint describing it as one transaction.

    A dry run has no record and nothing to commit, but still ends its read
    transaction between batches — scanning a large table would otherwise hold
    a single snapshot open for the whole run, with vacuum blocked behind it.
    """
    if record is None:
        await session.rollback()
        return
    # Reassigned, not mutated in place: SQLAlchemy cannot see a mutation
    # inside a plain JSONB dict, so an in-place update would never be flushed.
    record.result = progress.as_dict()
    session.add(record)
    await session.commit()


async def _throttle(seconds: float) -> None:
    """Inter-batch pause. Its own function so tests can observe (and skip) it."""
    if seconds > 0:
        await asyncio.sleep(seconds)


async def _rows_ahead(
    session: AsyncSession, scan: Table, target: EncryptedColumn, after: object | None
) -> int:
    """How many rows the scan of `target` still has to read — the remaining gauge's input."""
    stmt = select(func.count()).select_from(scan)
    if after is not None:
        stmt = stmt.where(scan.c[target.pk] > after)
    return int((await session.execute(stmt)).scalar_one())


def _build_report(
    targets: tuple[EncryptedColumn, ...],
    progress: _Progress,
    *,
    dry_run: bool,
    resumed: bool,
    record: JobRecord | None,
) -> dict[str, Any]:
    """The run's summary. In a dry run, `rotated` is what *would* be rewritten."""
    columns: list[dict[str, Any]] = [
        {
            "table": target.table,
            "column": target.column,
            "scanned": progress.scanned.get(target.label, 0),
            "rotated": progress.rotated.get(target.label, 0),
        }
        for target in targets
    ]
    return {
        "dry_run": dry_run,
        "resumed": resumed,
        "job_record_id": str(record.id) if record is not None else None,
        "columns": columns,
        "scanned_total": sum(column["scanned"] for column in columns),
        "rotated_total": sum(column["rotated"] for column in columns),
    }


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _RunConfig:
    dry_run: bool
    batch_size: int
    pause_seconds: float
    primary: Fernet
    all_keys: MultiFernet


@dataclass
class _Rotation:
    """One invocation's worth of scanning, carried across the columns it walks.

    A class rather than a pile of parameters because every step needs the same
    five-ish things (session, config, checkpoint record, progress, and the
    remaining-row countdown behind the gauge), and the batch loop mutates the
    last two as it goes.
    """

    session: AsyncSession
    config: _RunConfig
    record: JobRecord | None
    progress: _Progress
    redis_client: Redis
    rows_left: int = 0

    async def count_rows_left(self, pending: list[tuple[EncryptedColumn, Table]]) -> None:
        self.rows_left = 0
        for target, scan in pending:
            resume_point = self.progress.resume_point(target)
            self.rows_left += await _rows_ahead(
                self.session,
                scan,
                target,
                None if resume_point is None else _decode_pk(scan, target, resume_point),
            )
        await self._publish_rows_left()

    async def rotate_column(self, target: EncryptedColumn, scan: Table) -> None:
        primary_key_column = scan.c[target.pk]
        resume_point = self.progress.resume_point(target)
        after: Any = None if resume_point is None else _decode_pk(scan, target, resume_point)
        batch_update = (
            update(scan)
            .where(primary_key_column == bindparam(_PK_BIND))
            .values({target.column: bindparam(_VALUE_BIND, type_=Text)})
        )

        while True:
            batch_started = time.perf_counter()
            stmt = select(primary_key_column, scan.c[target.column]).order_by(primary_key_column)
            if after is not None:
                stmt = stmt.where(primary_key_column > after)
            rows = (await self.session.execute(stmt.limit(self.config.batch_size))).all()
            if not rows:
                break

            after, rotated_in_batch = await self._apply_batch(target, batch_update, rows)
            await self._record_batch(
                target,
                rows_in_batch=len(rows),
                rotated_in_batch=rotated_in_batch,
                started=batch_started,
            )
            if len(rows) < self.config.batch_size:
                break  # short page — the table ends here, no empty round trip needed
            await _throttle(self.config.pause_seconds)

        self.progress.done_columns.append(target.label)
        self.progress.cursor = None
        await _commit_checkpoint(self.session, self.record, self.progress)

    async def _apply_batch(
        self, target: EncryptedColumn, batch_update: Update, rows: Sequence[Row[Any]]
    ) -> tuple[Any, int]:
        """Rewrites what needs rewriting and checkpoints it. Returns (last PK, rows rewritten)."""
        updates: list[dict[str, Any]] = []
        last_pk: Any = None
        for row_pk, raw_value in rows:
            last_pk = row_pk
            new_ciphertext = _reencrypted_value(
                raw_value, self.config.primary, self.config.all_keys
            )
            if new_ciphertext is not None:
                updates.append({_PK_BIND: row_pk, _VALUE_BIND: new_ciphertext})

        if updates and not self.config.dry_run:
            # One executemany round trip for the batch rather than a statement
            # per row: the difference is minutes per million rows, all of it
            # pool time held against live traffic.
            await self.session.execute(batch_update, updates)

        self.progress.scanned[target.label] = self.progress.scanned.get(target.label, 0) + len(rows)
        self.progress.rotated[target.label] = self.progress.rotated.get(target.label, 0) + len(
            updates
        )
        self.progress.cursor = {
            "table": target.table,
            "column": target.column,
            "last_pk": str(last_pk),
        }
        # Batch and checkpoint commit together: the cursor can never describe
        # rows whose UPDATE didn't land, nor land rows the cursor doesn't cover.
        await _commit_checkpoint(self.session, self.record, self.progress)
        return last_pk, len(updates)

    async def _publish_rows_left(self) -> None:
        """Mirrors the countdown into Redis for FORGE_REENCRYPT_REMAINING to serve.

        Best-effort by construction: a rotation that is going fine must not be
        derailed by an unreachable Redis, and the JobRecord checkpoint -- not
        this -- is the authoritative progress record.
        """
        try:
            await self.redis_client.set(REENCRYPT_REMAINING_REDIS_KEY, self.rows_left)
        except RedisError:
            logger.warning(
                "%s: could not publish the remaining-rows gauge to Redis; "
                "progress is still recorded on the JobRecord",
                JOB_TYPE,
                exc_info=True,
            )

    async def _record_batch(
        self, target: EncryptedColumn, *, rows_in_batch: int, rotated_in_batch: int, started: float
    ) -> None:
        labels = {"table": target.table, "column": target.column}
        FORGE_REENCRYPT_BATCH_SECONDS.labels(**labels).observe(time.perf_counter() - started)
        if not self.config.dry_run:
            FORGE_REENCRYPT_ROWS_TOTAL.labels(**labels).inc(rotated_in_batch)
        self.rows_left = max(self.rows_left - rows_in_batch, 0)
        await self._publish_rows_left()
        logger.info(
            "%s: batch done (column=%s, scanned=%d, rewritten=%d, rows_left=%d)",
            JOB_TYPE,
            target.label,
            self.progress.scanned[target.label],
            self.progress.rotated[target.label],
            self.rows_left,
        )


# ---------------------------------------------------------------------------
# The job
# ---------------------------------------------------------------------------


@trace_job
async def rotate_encryption_keys_job(  # noqa: PLR0913 -- ARQ's ctx plus the five knobs the runbook drives this with; a settings object would just move them
    ctx: dict[str, Any],  # noqa: ARG001 -- ARQ calling convention
    *,
    dry_run: bool = False,
    batch_size: int | None = None,
    throttle_seconds: float | None = None,
    resume: bool = True,
    only: list[str] | None = None,
) -> dict[str, Any]:
    """Walks every encrypted column, rewriting it onto the primary ENCRYPTION_KEY.

    `only` restricts the run to specific "table.column" labels — the one
    column at a time mode the runbook starts with. `dry_run` counts the work
    without writing anything at all: no row updates, and no JobRecord either.
    `resume` (default on) continues the newest interrupted run instead of
    starting a fresh one.

    Returns the run's report, which is also stored on its JobRecord.
    """
    config = _run_config(dry_run=dry_run, batch_size=batch_size, throttle_seconds=throttle_seconds)
    targets = _selected_targets(only)
    redis_client = _progress_redis()
    try:
        report = await _run_rotation(targets, config, resume=resume, redis_client=redis_client)
    finally:
        await redis_client.aclose()
    logger.info("%s: finished %s", JOB_TYPE, report)
    return report


async def _run_rotation(
    targets: tuple[EncryptedColumn, ...],
    config: _RunConfig,
    *,
    resume: bool,
    redis_client: Redis,
) -> dict[str, Any]:
    dry_run = config.dry_run
    async with async_session_factory() as session:
        record: JobRecord | None = None
        resumed = False
        progress = _Progress()
        if not dry_run:
            record, resumed = await _open_record(
                session,
                resume=resume,
                payload={
                    "batch_size": config.batch_size,
                    "throttle_seconds": config.pause_seconds,
                    "columns": [target.label for target in targets],
                },
            )
            progress = _Progress.from_record(record)

        pending = [
            (target, _scan_table(target))
            for target in targets
            if target.label not in progress.done_columns
        ]
        rotation = _Rotation(
            session=session,
            config=config,
            record=record,
            progress=progress,
            redis_client=redis_client,
        )
        await rotation.count_rows_left(pending)
        logger.info(
            "%s: starting (dry_run=%s, resumed=%s, columns=%s, rows_to_scan=%d, batch_size=%d)",
            JOB_TYPE,
            dry_run,
            resumed,
            [target.label for target, _ in pending],
            rotation.rows_left,
            config.batch_size,
        )

        for target, scan in pending:
            await rotation.rotate_column(target, scan)

        report = _build_report(targets, progress, dry_run=dry_run, resumed=resumed, record=record)
        if record is not None:
            record.status = _STATUS_DONE
            record.completed_at = utcnow()
            record.result = {**progress.as_dict(), "report": report}
            session.add(record)
            await session.commit()

    return report


def _progress_redis() -> Redis:
    """The client _Rotation publishes its remaining-rows gauge through.

    Built per run rather than pulled from ARQ's ctx: this job is normally
    driven as a one-off process (see the CLI at the bottom of this module),
    which has no ctx to take one from. Connecting is lazy in redis.asyncio, so
    an unreachable Redis costs nothing until the first publish -- which is
    caught and logged, never raised.
    """
    return cast("Redis", Redis.from_url(str(get_settings().REDIS_URL)))


def _run_config(
    *, dry_run: bool, batch_size: int | None, throttle_seconds: float | None
) -> _RunConfig:
    settings = get_settings()
    effective_batch_size = (
        batch_size if batch_size is not None else settings.ENCRYPTION_ROTATION_BATCH_SIZE
    )
    if effective_batch_size < 1:
        raise ValueError(f"batch_size must be at least 1, got {effective_batch_size}")
    return _RunConfig(
        dry_run=dry_run,
        batch_size=effective_batch_size,
        pause_seconds=(
            throttle_seconds
            if throttle_seconds is not None
            else settings.ENCRYPTION_ROTATION_THROTTLE_SECONDS
        ),
        # Fernet(primary) alone, not the MultiFernet: "is this row already on
        # the new key?" is a question only the primary key can answer, and
        # MultiFernet.decrypt would happily answer it with an old one.
        primary=Fernet(settings.ENCRYPTION_KEY.encode("ascii")),
        all_keys=get_shared_fernet(),
    )


def _selected_targets(only: list[str] | None) -> tuple[EncryptedColumn, ...]:
    targets = discover_encrypted_columns()
    if only is None:
        return targets
    wanted = set(only)
    unknown = wanted - {target.label for target in targets}
    if unknown:
        raise ValueError(
            f"No such encrypted column(s): {', '.join(sorted(unknown))}. "
            f"Known columns: {', '.join(target.label for target in targets)}"
        )
    return tuple(target for target in targets if target.label in wanted)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
#
# A rotation runs as a one-off process (`python -m app.workers.jobs.encryption`),
# not through the queue: WorkerSettings.job_timeout is 300s, and a scan of a
# multi-million-row table isn't a five-minute job. The checkpoint makes that a
# preference rather than a rule -- an ARQ-enqueued run that gets killed at the
# timeout resumes correctly -- but a foreground process is what the runbook
# drives, because it's what an operator can watch.


async def _main(argv: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(
        prog="python -m app.workers.jobs.encryption",
        description="Re-encrypt every EncryptedString column onto the current ENCRYPTION_KEY.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="count the work, write nothing at all"
    )
    parser.add_argument("--batch-size", type=int, default=None, help="rows per batch")
    parser.add_argument(
        "--throttle",
        type=float,
        default=None,
        dest="throttle_seconds",
        help="seconds between batches",
    )
    parser.add_argument(
        "--only",
        action="append",
        metavar="TABLE.COLUMN",
        help="restrict to one column (repeatable); defaults to every discovered column",
    )
    parser.add_argument(
        "--no-resume",
        action="store_false",
        dest="resume",
        help="start a new run instead of continuing an interrupted one",
    )
    args = parser.parse_args(argv)

    report = await rotate_encryption_keys_job(
        {},
        dry_run=args.dry_run,
        batch_size=args.batch_size,
        throttle_seconds=args.throttle_seconds,
        resume=args.resume,
        only=args.only,
    )
    print(json.dumps(report, indent=2))  # noqa: T201 -- this *is* the CLI output
    await dispose_engine()
    return report


if __name__ == "__main__":
    asyncio.run(_main())
