"""
app.workers.jobs.storage: the physical deletion that StorageService.delete_file
has been enqueueing, by name, since T-112 -- against a worker that had no
function by that name. Every deleted file's object stayed in the bucket.

Three entry points. Two are idempotent and both re-check `deleted_at`
before they touch anything: `hard_delete_stored_file` (the deferred job,
seven days after the soft delete) and `sweep_orphaned_objects` (the daily
reconciliation pass for the ones whose deferred job was lost). The third,
`expire_abandoned_uploads`, is the other half of counting pending uploads
toward the storage quota -- without it a client that starts an upload and
closes the tab holds its org's allowance down forever.

Same real-Postgres testcontainers shape as test_virus_scan_job.py: the job
module's own `async_session_factory` is monkeypatched onto a dedicated test
engine, and StorageBackend is faked -- the S3 wire protocol is
test_storage_backend.py's concern, and what matters here is the ordering
(object first, row second) and the conditions under which nothing happens at
all.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from app.models._mixins import utcnow
from app.modules.auth.models import User
from app.modules.storage.keys import upload_key
from app.modules.storage.models import StoredFile
from app.modules.storage.quota import UPLOAD_RESERVATION_WINDOW
from app.modules.storage.service import HARD_DELETE_GRACE_PERIOD
from app.modules.tenancy.models import Organization
from app.workers.jobs import storage as storage_job_module
from app.workers.jobs.storage import (
    expire_abandoned_uploads,
    hard_delete_stored_file,
    rescan_unscanned_files,
    sweep_orphaned_objects,
)

pytestmark = pytest.mark.module_dependent  # stored_file


@pytest.fixture(autouse=True)
def _use_test_engine(
    monkeypatch: pytest.MonkeyPatch, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    monkeypatch.setattr(storage_job_module, "async_session_factory", session_factory)


@dataclass
class _FakeBackend:
    deleted: list[str] = field(default_factory=list)
    fail_on: set[str] = field(default_factory=set)

    async def delete(self, key: str) -> None:
        if key in self.fail_on:
            raise RuntimeError("object store is unavailable")
        self.deleted.append(key)


@pytest.fixture
def fake_backend(monkeypatch: pytest.MonkeyPatch) -> _FakeBackend:
    backend = _FakeBackend()
    monkeypatch.setattr(storage_job_module, "get_storage_backend", lambda: backend)
    return backend


@pytest.fixture
def ctx() -> dict[str, Any]:
    return {}


async def _make_file(session: AsyncSession, *, deleted_ago: timedelta | None = None) -> StoredFile:
    org = Organization(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    session.add(org)
    user = User(email=f"user-{uuid4()}@example.com")
    session.add(user)
    await session.flush()

    stored = StoredFile(
        org_id=org.id,
        uploaded_by=user.id,
        key=f"{org.id}/default/{uuid4()}/report.pdf",
        filename="report.pdf",
        content_type="application/pdf",
        size_bytes=1024,
        status="ready",
        virus_scan_result="clean",
        deleted_at=None if deleted_ago is None else utcnow() - deleted_ago,
    )
    session.add(stored)
    await session.commit()
    return stored


# --- hard_delete_stored_file -----------------------------------------------------------------


async def test_it_removes_the_object_and_the_row(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    ctx: dict[str, Any],
    fake_backend: _FakeBackend,
) -> None:
    stored = await _make_file(session, deleted_ago=HARD_DELETE_GRACE_PERIOD)

    await hard_delete_stored_file(ctx, file_id=stored.id)

    # The object and anything left at its upload key (review R1).
    assert fake_backend.deleted == [stored.key, upload_key(stored.key)]
    async with session_factory() as verify:
        assert await verify.get(StoredFile, stored.id) is None


async def test_a_cancelled_deletion_survives_its_own_job(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    ctx: dict[str, Any],
    fake_backend: _FakeBackend,
) -> None:
    # The grace window exists so a deletion can be undone. The deferred job
    # still fires at the end of it, and must do nothing.
    stored = await _make_file(session, deleted_ago=None)

    await hard_delete_stored_file(ctx, file_id=stored.id)

    assert fake_backend.deleted == []
    async with session_factory() as verify:
        assert await verify.get(StoredFile, stored.id) is not None


async def test_running_twice_is_not_an_error(
    session: AsyncSession, ctx: dict[str, Any], fake_backend: _FakeBackend
) -> None:
    # arq retries, and the sweep can reach the same row from the other side.
    stored = await _make_file(session, deleted_ago=HARD_DELETE_GRACE_PERIOD)

    await hard_delete_stored_file(ctx, file_id=stored.id)
    await hard_delete_stored_file(ctx, file_id=stored.id)

    # The object and anything left at its upload key (review R1).
    assert fake_backend.deleted == [stored.key, upload_key(stored.key)]


async def test_an_unknown_file_is_not_an_error(
    ctx: dict[str, Any], fake_backend: _FakeBackend
) -> None:
    await hard_delete_stored_file(ctx, file_id=uuid4())

    assert fake_backend.deleted == []


async def test_the_row_survives_when_the_object_cannot_be_deleted(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    ctx: dict[str, Any],
    fake_backend: _FakeBackend,
) -> None:
    # Object first, row second, and never the reverse: dropping the row while
    # the object survives loses the only record of what to delete, and leaks
    # it permanently. So a failing object store leaves work for the sweep.
    stored = await _make_file(session, deleted_ago=HARD_DELETE_GRACE_PERIOD)
    fake_backend.fail_on.add(stored.key)

    with pytest.raises(RuntimeError):
        await hard_delete_stored_file(ctx, file_id=stored.id)

    async with session_factory() as verify:
        assert await verify.get(StoredFile, stored.id) is not None


# --- sweep_orphaned_objects ------------------------------------------------------------------


async def test_the_sweep_collects_files_whose_job_never_ran(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    ctx: dict[str, Any],
    fake_backend: _FakeBackend,
) -> None:
    # The backlog case: rows soft-deleted long ago whose deferred job was
    # lost -- or, as it happens, never existed.
    stale = await _make_file(session, deleted_ago=HARD_DELETE_GRACE_PERIOD + timedelta(days=30))

    await sweep_orphaned_objects(ctx)

    # The object and anything left at its upload key (review R1).
    assert fake_backend.deleted == [stale.key, upload_key(stale.key)]
    async with session_factory() as verify:
        assert await verify.get(StoredFile, stale.id) is None


async def test_the_sweep_leaves_the_grace_window_alone(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    ctx: dict[str, Any],
    fake_backend: _FakeBackend,
) -> None:
    # Still recoverable, and its deferred job is still pending. Deleting it
    # here would silently shorten the undo window to "until 04:00".
    recent = await _make_file(session, deleted_ago=timedelta(hours=1))

    await sweep_orphaned_objects(ctx)

    assert fake_backend.deleted == []
    async with session_factory() as verify:
        assert await verify.get(StoredFile, recent.id) is not None


async def test_the_sweep_ignores_live_files(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    ctx: dict[str, Any],
    fake_backend: _FakeBackend,
) -> None:
    live = await _make_file(session, deleted_ago=None)

    await sweep_orphaned_objects(ctx)

    assert fake_backend.deleted == []
    async with session_factory() as verify:
        assert await verify.get(StoredFile, live.id) is not None


async def test_the_sweep_is_bounded_per_tick(
    session: AsyncSession,
    ctx: dict[str, Any],
    fake_backend: _FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A backlog is drained over several ticks rather than in one long
    # transaction holding a connection from a pool of 20.
    monkeypatch.setattr(storage_job_module, "_SWEEP_BATCH_SIZE", 2)
    for _ in range(3):
        await _make_file(session, deleted_ago=HARD_DELETE_GRACE_PERIOD + timedelta(days=1))

    await sweep_orphaned_objects(ctx)

    # Two files, each deleted at its key and its upload key (review R1).
    assert len(fake_backend.deleted) == 4


async def test_an_empty_sweep_does_nothing(ctx: dict[str, Any], fake_backend: _FakeBackend) -> None:
    await sweep_orphaned_objects(ctx)

    assert fake_backend.deleted == []


# --- rescan_unscanned_files ------------------------------------------------------------------
#
# The migration path for the download gate. Files confirmed before the gate
# existed have no verdict, so they all became undownloadable on the build
# that added it -- correctly, since they had never been scanned, but they
# need a way back.


async def _make_scanned_file(
    session: AsyncSession, *, status: str, verdict: str | None
) -> StoredFile:
    stored = await _make_file(session)
    stored.status = status
    stored.virus_scan_result = verdict
    await session.commit()
    return stored


async def test_it_enqueues_a_scan_for_a_confirmed_file_with_no_verdict(
    session: AsyncSession,
    ctx: dict[str, Any],
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    stored = await _make_scanned_file(session, status="ready", verdict=None)

    enqueued, _ = await rescan_unscanned_files(ctx)

    assert enqueued == 1
    assert ("virus_scan", {"file_id": stored.id}) in enqueued_jobs


@pytest.mark.parametrize(
    ("status", "verdict"),
    [
        ("ready", "clean"),  # already has a verdict
        ("infected", "Eicar-Test-Signature"),  # a verdict was reached
        ("failed", None),  # a verdict was attempted and failed
        ("pending", None),  # never finished uploading
    ],
)
async def test_it_leaves_every_other_state_alone(
    session: AsyncSession,
    ctx: dict[str, Any],
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
    status: str,
    verdict: str | None,
) -> None:
    # "infected" and "failed" matter most here: re-running those would be a
    # retry loop that could eventually flip a known-bad file to downloadable.
    await _make_scanned_file(session, status=status, verdict=verdict)

    enqueued, _ = await rescan_unscanned_files(ctx)

    assert enqueued == 0
    assert [name for name, _ in enqueued_jobs if name == "virus_scan"] == []


async def test_it_ignores_deleted_files(session: AsyncSession, ctx: dict[str, Any]) -> None:
    stored = await _make_scanned_file(session, status="ready", verdict=None)
    stored.deleted_at = utcnow()
    await session.commit()

    assert (await rescan_unscanned_files(ctx))[0] == 0


async def test_it_is_batched_so_a_backlog_can_be_drained_in_steps(
    session: AsyncSession, ctx: dict[str, Any]
) -> None:
    # The first run against a real deployment is a backlog the size of the
    # bucket; the caller loops until this returns 0.
    for _ in range(3):
        await _make_scanned_file(session, status="ready", verdict=None)

    assert (await rescan_unscanned_files(ctx, limit=2))[0] == 2


# --- the file_storage toggle -----------------------------------------------------------------


@pytest.mark.parametrize(
    "job",
    [hard_delete_stored_file, sweep_orphaned_objects, rescan_unscanned_files],
)
async def test_every_job_no_ops_when_file_storage_is_off(
    ctx: dict[str, Any],
    fake_backend: _FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
    job: Callable[..., Awaitable[object]],
) -> None:
    """`stored_file` does not exist in a product that switched storage off.

    These are registered unconditionally -- in WorkerSettings.functions and
    on the scheduler -- so the guard has to be in the function, the same
    shape prune_webhook_deliveries uses. Without it the minimum-surface
    configuration gets a nightly job failing on a missing relation.
    """
    monkeypatch.setattr(
        storage_job_module, "is_enabled", lambda name: name != "file_storage", raising=True
    )

    kwargs = {"file_id": uuid4()} if job is hard_delete_stored_file else {}
    await job(ctx, **kwargs)

    assert fake_backend.deleted == []


async def test_the_drain_loop_moves_past_rows_it_has_already_enqueued(
    session: AsyncSession,
    ctx: dict[str, Any],
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    """The bug this cursor exists for, reproduced.

    Enqueuing a scan does not change the row -- the worker does that later,
    asynchronously -- so a drain loop that just re-ran the query selected the
    same rows over and over until the worker caught up. On a real deployment
    with five unscanned files it enqueued 682 jobs before settling.

    A second call carrying the first's cursor must return nothing, even
    though every row still looks exactly as unscanned as it did before.
    """
    for _ in range(3):
        await _make_scanned_file(session, status="ready", verdict=None)

    first, cursor = await rescan_unscanned_files(ctx, limit=2)
    assert first == 2
    assert cursor is not None

    second, _ = await rescan_unscanned_files(ctx, limit=2, after=cursor)

    # The third row, and only the third -- not the two already enqueued.
    assert second == 1
    scans = [kwargs["file_id"] for name, kwargs in enqueued_jobs if name == "virus_scan"]
    assert len(scans) == len(set(scans)), "a file was enqueued for scanning twice"


async def test_the_cursor_is_stable_when_rows_share_a_timestamp(
    session: AsyncSession, ctx: dict[str, Any]
) -> None:
    """Keyset on `(created_at, id)`, not on `created_at` alone.

    A bulk import writes rows in the same instant, so a timestamp-only
    cursor would either skip the rest of that instant or loop on it forever.
    """
    shared = utcnow()
    for _ in range(4):
        stored = await _make_scanned_file(session, status="ready", verdict=None)
        stored.created_at = shared
    await session.commit()

    seen: list[UUID] = []
    cursor = None
    # Bounded, and the bound is the assertion. A cursor that fails to
    # advance makes this loop run forever, and a hanging test says far less
    # than a failing one -- four rows at two per batch is two batches plus
    # the empty one that ends it, so anything past five is the bug.
    for _ in range(5):
        count, cursor = await rescan_unscanned_files(ctx, limit=2, after=cursor)
        if count == 0:
            break
        assert cursor is not None
        seen.append(cursor[1])
    else:
        pytest.fail("cursor never reached the end of the table -- it is not advancing")

    assert len(seen) == len(set(seen))


# --- expire_abandoned_uploads ----------------------------------------------------------------
#
# Counting a pending row toward the quota (app.modules.storage.quota) is
# what stops concurrent presigns spending the same free space twice. This
# job is what stops that fix becoming a leak: a reservation nobody confirms
# has to be given back.


async def _make_pending_upload(session: AsyncSession, *, age: timedelta) -> StoredFile:
    stored = await _make_file(session)
    stored.status = "pending"
    stored.virus_scan_result = None
    stored.created_at = utcnow() - age
    session.add(stored)
    await session.commit()
    return stored


async def test_an_abandoned_presign_is_retired(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    ctx: dict[str, Any],
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    stored = await _make_pending_upload(
        session, age=UPLOAD_RESERVATION_WINDOW + timedelta(minutes=5)
    )

    await expire_abandoned_uploads(ctx)

    async with session_factory() as verify:
        refreshed = await verify.get(StoredFile, stored.id)
        assert refreshed is not None
        assert refreshed.deleted_at is not None
    # Soft-deleted, then handed to the same physical-deletion path every
    # other deletion uses -- and without the seven-day defer, because there
    # is no user decision here to undo.
    assert ("hard_delete_stored_file", {"file_id": stored.id}) in enqueued_jobs


async def test_an_upload_still_inside_its_window_is_left_alone(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    ctx: dict[str, Any],
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    """A presigned URL bounds when an upload may *start*, not when it must
    finish. A PUT begun a minute before expiry can still be streaming, and
    S3 will accept it -- so expiring early would uncount bytes that are
    actively arriving."""
    stored = await _make_pending_upload(
        session, age=UPLOAD_RESERVATION_WINDOW - timedelta(minutes=5)
    )

    await expire_abandoned_uploads(ctx)

    async with session_factory() as verify:
        refreshed = await verify.get(StoredFile, stored.id)
        assert refreshed is not None
        assert refreshed.deleted_at is None
    assert enqueued_jobs == []


async def test_a_confirmed_file_is_never_expired(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    ctx: dict[str, Any],
) -> None:
    """Only `pending` rows are reservations. An old `ready` file is just an
    old file, and deleting one here would be data loss rather than cleanup
    -- which is why the age check is paired with the status check and not
    used alone."""
    stored = await _make_file(session)
    stored.created_at = utcnow() - UPLOAD_RESERVATION_WINDOW * 100
    session.add(stored)
    await session.commit()

    await expire_abandoned_uploads(ctx)

    async with session_factory() as verify:
        refreshed = await verify.get(StoredFile, stored.id)
        assert refreshed is not None
        assert refreshed.deleted_at is None


async def test_it_is_idempotent(
    session: AsyncSession,
    ctx: dict[str, Any],
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    """The second tick must find nothing: the first one soft-deleted the
    row, and the query excludes soft-deleted rows. Without that clause the
    job would re-enqueue a hard delete every hour until the row was gone."""
    await _make_pending_upload(session, age=UPLOAD_RESERVATION_WINDOW + timedelta(minutes=5))

    await expire_abandoned_uploads(ctx)
    first = len(enqueued_jobs)
    await expire_abandoned_uploads(ctx)

    assert len(enqueued_jobs) == first == 1


async def test_it_drains_a_backlog_in_batches(
    session: AsyncSession,
    ctx: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    """Bounded per tick, like the orphan sweep: a backlog is drained over
    several runs rather than in one transaction holding a connection from a
    pool of twenty."""
    monkeypatch.setattr(storage_job_module, "_SWEEP_BATCH_SIZE", 2)
    for _ in range(5):
        await _make_pending_upload(session, age=UPLOAD_RESERVATION_WINDOW + timedelta(minutes=5))

    await expire_abandoned_uploads(ctx)

    assert len(enqueued_jobs) == 2
