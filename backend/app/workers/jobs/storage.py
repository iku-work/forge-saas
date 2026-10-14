"""
ARQ jobs for the storage module.

hard_delete_stored_file: the physical half of StorageService.delete_file.
That method soft-deletes the row and enqueues this job with a seven-day
defer, so a deletion can still be undone during the grace window; this is
what finally removes the object from the bucket and the row from the table.

It is written now because it was never written then. `delete_file` has been
enqueueing `"hard_delete_stored_file"` by name since T-112, against a worker
that has never had a function by that name registered -- arq resolves the
name at run time and simply logs an unresolved-job error, so every deletion
since has quietly left its object in the bucket. The row's quota
contribution disappeared with the soft delete while the bytes stayed, which
means repeated upload/delete cycles could grow real storage without ever
moving the number the quota check reads.

`sweep_orphaned_objects` exists because of that history rather than in spite
of it: a deferred arq job is a single Redis entry, and anything that loses
it -- a flushed queue, a Redis failover, a worker that was not running when
the defer elapsed, or the two years this template spent enqueueing a job
nobody had implemented -- loses the deletion with no trace. The sweep is the
reconciliation pass that makes the delayed job an optimisation rather than
the only thing standing between a deleted file and a bill for it.

Both are idempotent. Both re-check `deleted_at` before touching anything, so
a file whose deletion was cancelled during the grace window survives even if
its job still fires.

`expire_abandoned_uploads` is the other half of counting pending uploads
toward the quota (app.modules.storage.quota). A reservation that never
expires is a slow leak of an org's storage allowance, paid for by every
client that started an upload and walked away.

`retire_unused_images` removes avatars and logos nothing points at any more.
Choosing a new photo (or logo), or removing one, only rewrites the key on the
user (or organization) row; the old file stayed, counting towards its org's
storage for good. It is the same soft-delete-then-hard-delete path as any
deletion, after a day's grace so a photo uploaded but not yet chosen (the
page sets it a moment after the upload confirms) is never caught.

`rescan_unscanned_files` is the migration path for the download gate that
now requires a clean verdict (app.modules.storage.scan_gate). Every file
uploaded before that gate existed has `virus_scan_result` NULL -- they were
confirmed and then never actually scanned, which is the whole finding -- so
on the build that introduces the gate they all become undownloadable at
once. This re-enqueues a scan for them. It is operator-triggered rather than
scheduled: the first run is a backlog the size of the bucket, and whoever
runs it should be watching a real scanner come up behind it.
"""

import argparse
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import jobs
from app.core.db import async_session_factory, dispose_engine
from app.core.module_loader import is_enabled
from app.core.tracing import trace_job
from app.models._mixins import utcnow
from app.modules.auth.models import User
from app.modules.storage.avatars import AVATAR_BUCKET
from app.modules.storage.backend import StorageBackend, get_storage_backend
from app.modules.storage.keys import delete_every_copy
from app.modules.storage.logos import LOGO_BUCKET
from app.modules.storage.models import StoredFile
from app.modules.storage.quota import reservation_cutoff

# Imported, never redeclared: the sweep and the deferred job have to agree on
# the grace window, or the sweep deletes objects whose window is still open
# and whose deferred job is still pending.
from app.modules.storage.service import HARD_DELETE_GRACE_PERIOD
from app.modules.tenancy.models import Organization

logger = logging.getLogger(__name__)

# Bounds one sweep's work: a backlog is drained over several ticks rather
# than in one long transaction holding a connection from a pool of 20.
_SWEEP_BATCH_SIZE = 200

# How long an uploaded avatar or logo may sit unchosen before it counts as
# unused: far longer than the moment between an upload and the PATCH that
# chooses it.
_UNUSED_IMAGE_GRACE = timedelta(days=1)

# One rescan batch. Deliberately larger than the sweep's: this enqueues jobs
# rather than doing the work, and the queue is what paces the scanner.
_RESCAN_DEFAULT_LIMIT = 500


@trace_job
async def hard_delete_stored_file(
    ctx: dict[str, Any],  # noqa: ARG001 -- ARQ calling convention
    *,
    file_id: UUID,
) -> None:
    if not is_enabled("file_storage"):
        # Same shape as prune_webhook_deliveries': registered unconditionally,
        # guarded here. `stored_file` does not exist in a product that
        # switched file storage off, so a job that assumed otherwise would
        # fail on a missing relation rather than no-op.
        return
    async with async_session_factory() as session:
        stored_file = await session.get(StoredFile, file_id)
        if stored_file is None:
            # Already hard-deleted, by an earlier run of this job or by the
            # sweep. Idempotent by design: arq retries, and the sweep can
            # reach the same row from the other direction.
            logger.info("hard_delete_stored_file: file_id=%s already gone", file_id)
            return
        if stored_file.deleted_at is None:
            # The deletion was cancelled during the grace window. Not an
            # error -- that window exists precisely so this can happen.
            logger.info("hard_delete_stored_file: file_id=%s no longer deleted, skipping", file_id)
            return

        await _purge(session, get_storage_backend(), stored_file)
        await session.commit()


async def sweep_orphaned_objects(
    ctx: dict[str, Any],  # noqa: ARG001 -- scheduler calling convention
) -> None:
    """Hard-delete soft-deleted files whose deferred job never ran."""
    if not is_enabled("file_storage"):
        return
    cutoff = utcnow() - HARD_DELETE_GRACE_PERIOD
    model = cast("Any", StoredFile)

    async with async_session_factory() as session:
        stmt = (
            select(StoredFile)
            .where(model.deleted_at.is_not(None), model.deleted_at < cutoff)
            .order_by(model.deleted_at)
            .limit(_SWEEP_BATCH_SIZE)
        )
        rows = list((await session.execute(stmt)).scalars().all())
        if not rows:
            return

        backend = get_storage_backend()
        for stored_file in rows:
            await _purge(session, backend, stored_file)
        await session.commit()

    logger.info("sweep_orphaned_objects: hard-deleted %d file(s) past the grace period", len(rows))


async def expire_abandoned_uploads(
    ctx: dict[str, Any],  # noqa: ARG001 -- scheduler calling convention
) -> None:
    """Retire presigns nobody ever confirmed.

    A `pending` row holds quota space (app.modules.storage.quota) so that
    concurrent presigns cannot all spend the same free bytes. That
    reservation has to end, or a client that closed the tab mid-upload
    would hold an org's quota down forever -- which is the other half of
    counting reservations, and the half that turns a correctness fix into a
    denial-of-service if it is left out.

    Soft-delete rather than hard: identical to StorageService._expire, so
    the object and row are removed by the same two paths that clean up
    every other deletion. `hard_delete_stored_file` is enqueued per row
    with no defer -- nothing here is undoable.

    Runs hourly, against a window of an hour, so a reservation lives
    between one and two hours. Erring long is the right direction: the
    failure of expiring too early is refusing an upload that is still
    arriving.
    """
    if not is_enabled("file_storage"):
        return
    model = cast("Any", StoredFile)

    async with async_session_factory() as session:
        stmt = (
            select(StoredFile)
            .where(
                model.status == "pending",
                model.deleted_at.is_(None),
                model.created_at <= reservation_cutoff(),
            )
            .order_by(model.created_at)
            .limit(_SWEEP_BATCH_SIZE)
        )
        rows = list((await session.execute(stmt)).scalars().all())
        if not rows:
            return

        now = utcnow()
        for stored_file in rows:
            stored_file.deleted_at = now
            session.add(stored_file)
        await session.commit()

    for stored_file in rows:
        await jobs.enqueue_job("hard_delete_stored_file", file_id=stored_file.id)

    logger.info("expire_abandoned_uploads: expired %d unconfirmed upload(s)", len(rows))


async def retire_unused_images(
    ctx: dict[str, Any],  # noqa: ARG001 -- scheduler calling convention
) -> None:
    """Delete avatars and logos that no user or organization points at."""
    if not is_enabled("file_storage"):
        return
    model = cast("Any", StoredFile)
    user = cast("Any", User)
    org = cast("Any", Organization)
    in_use = (
        select(user.avatar_object_key)
        .where(user.avatar_object_key.is_not(None))
        .union(select(org.logo_object_key).where(org.logo_object_key.is_not(None)))
    )

    async with async_session_factory() as session:
        stmt = (
            select(StoredFile)
            .where(
                model.bucket.in_((AVATAR_BUCKET, LOGO_BUCKET)),
                # Pending uploads are expire_abandoned_uploads' to retire.
                model.status != "pending",
                model.deleted_at.is_(None),
                model.created_at <= utcnow() - _UNUSED_IMAGE_GRACE,
                model.key.not_in(in_use),
            )
            .order_by(model.created_at)
            .limit(_SWEEP_BATCH_SIZE)
        )
        rows = list((await session.execute(stmt)).scalars().all())
        if not rows:
            return

        now = utcnow()
        for stored_file in rows:
            stored_file.deleted_at = now
            session.add(stored_file)
        await session.commit()

    for stored_file in rows:
        await jobs.enqueue_job("hard_delete_stored_file", file_id=stored_file.id)

    logger.info("retire_unused_images: retired %d unused avatar/logo file(s)", len(rows))


async def _purge(session: AsyncSession, backend: StorageBackend, stored_file: StoredFile) -> None:
    """Remove the object, then the row -- in that order, and never the reverse.

    If the object delete fails the row stays, so the sweep retries it. The
    opposite order would drop the only record of which object to delete and
    leak it permanently, which is the failure this whole module exists to
    stop.

    An object that is already absent is a success: "infected" files are
    deleted by the scan job at verdict time, and a retry of this job has
    normally already removed it.
    """
    await delete_every_copy(backend, stored_file.key)
    await session.delete(stored_file)


async def rescan_unscanned_files(
    ctx: dict[str, Any],  # noqa: ARG001 -- ARQ/CLI calling convention
    *,
    limit: int = _RESCAN_DEFAULT_LIMIT,
    after: tuple[datetime, UUID] | None = None,
) -> tuple[int, tuple[datetime, UUID] | None]:
    """Re-enqueue a scan for confirmed files that never got a verdict.

    Returns (how many were enqueued, a cursor to continue from).

    **The cursor is the whole point, and it is not an optimisation.**
    Enqueuing a scan does not change the row -- the worker does that, later,
    asynchronously -- so a caller that simply re-ran this query would select
    the same rows again and again until the worker happened to catch up.
    Draining a backlog that way is not slow, it is unbounded: run against
    five unscanned files on a real deployment, the loop enqueued 682 jobs
    before the worker finished the first five. At a hundred thousand files
    it would be millions of duplicate jobs and a Redis problem rather than a
    storage one.

    Keyset on `(created_at, id)`, per §0.3 -- so each batch moves past the
    last one whether or not anything has been scanned in the meantime.

    Only touches rows that are `ready` with no `virus_scan_result`: a file
    already marked clean is left alone, an `infected` or `failed` one is a
    verdict that was reached and must not be quietly retried into
    downloadable, and a `pending` one never finished uploading.
    """
    if not is_enabled("file_storage"):
        return 0, None
    model = cast("Any", StoredFile)

    async with async_session_factory() as session:
        stmt = select(StoredFile).where(
            model.status == "ready",
            model.virus_scan_result.is_(None),
            model.deleted_at.is_(None),
        )
        if after is not None:
            # Row-value comparison rather than `created_at > x OR (= x AND
            # id > y)`: one index scan, and no ambiguity when several files
            # share a timestamp -- which they do, because a bulk import
            # writes them in the same instant.
            stmt = stmt.where(tuple_(model.created_at, model.id) > after)
        stmt = stmt.order_by(model.created_at, model.id).limit(limit)
        rows = list((await session.execute(stmt)).scalars().all())

    for stored_file in rows:
        await jobs.enqueue_job("virus_scan", file_id=stored_file.id)

    logger.info("rescan_unscanned_files: enqueued %d scan(s)", len(rows))
    cursor = (rows[-1].created_at, rows[-1].id) if rows else None
    return len(rows), cursor


async def _main(argv: list[str] | None = None) -> int:
    """`python -m app.workers.jobs.storage` -- drain the rescan backlog.

    Same shape as app.workers.jobs.encryption's CLI, and for the same
    reason: this is a one-off operator action with a backlog behind it, not
    something to schedule. It loops until nothing is left rather than making
    the operator run it repeatedly, and prints a count per batch so a long
    drain shows progress.
    """
    parser = argparse.ArgumentParser(description="Re-enqueue scans for unscanned files.")
    parser.add_argument("--batch-size", type=int, default=_RESCAN_DEFAULT_LIMIT)
    parser.add_argument(
        "--once",
        action="store_true",
        help="enqueue a single batch instead of draining the whole backlog",
    )
    args = parser.parse_args(argv)

    total = 0
    cursor: tuple[datetime, UUID] | None = None
    while True:
        enqueued, cursor = await rescan_unscanned_files({}, limit=args.batch_size, after=cursor)
        total += enqueued
        print(f"enqueued {enqueued} (total {total})")  # noqa: T201 -- this *is* the CLI output
        if enqueued == 0 or args.once:
            break

    await dispose_engine()
    return total


if __name__ == "__main__":
    asyncio.run(_main())
