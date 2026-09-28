"""
DB access for the storage module (T-112): org-scoped StoredFile reads/writes,
plus the `total_bytes_for_org` quota read StorageService.presign_upload
checks on every request.

That read used to be cached in Redis for 60 seconds, the way
NotificationRepository caches unread_count. It is not any more, and the
reason is worth keeping: an unread count is a display value, where a minute
of staleness costs nothing, while this one is the only number standing
between a client and the disk. It had no other reader -- the quota gate was
the sole caller -- so the cache bought nothing at all and cost a sixty-second
window in which every presign in the fleet could be authorised against the
same already-spent free space. `reserve_quota` serialises the check instead,
which is the guarantee a gate actually needs.

Adds its own list_cursor-shaped method (list_cursor_filtered) instead of
using BaseRepository.list_cursor directly, since the spec's `GET /files`
needs an optional `bucket` filter BaseRepository's generic version has no way
to express -- same reasoning as NotificationRepository.list_for_user's own
unread_only filter.
"""

from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.repository import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, BaseRepository
from app.modules.storage.models import StoredFile
from app.modules.storage.quota import counts_toward_quota


class StoredFileRepository(BaseRepository[StoredFile]):
    model = StoredFile

    def __init__(self, session: AsyncSession, org_id: UUID) -> None:
        super().__init__(session, org_id)

    async def lock_for_quota_check(self) -> None:
        """Serialise this org's quota checks for the rest of the transaction.

        Counting reservations (app.modules.storage.quota) fixes a presign
        that ignores the ones before it. It does not fix two presigns that
        run at the same instant: both read the total before either has
        written its row, both see room, both insert. A dropzone uploading
        three files concurrently -- which `FileUploader` does by default --
        hits that window on every single upload.

        A transaction-scoped Postgres advisory lock closes it without a
        table to contend on and without anything to release: it is held
        until commit or rollback, including the rollback `get_session`
        performs when a request raises. Uncontended it is a hash and a
        latch; contended it queues presigns for one org behind each other,
        which is exactly the intent.

        Raw SQL per §0.3: `pg_advisory_xact_lock` has no SQLAlchemy
        construct. The argument is bound, never interpolated. The org UUID
        is hashed to the bigint the function takes -- collisions between two
        orgs are possible and harmless, costing at worst a little needless
        serialisation between two tenants.
        """
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:org_key, 0))"),
            {"org_key": str(self.org_id)},
        )

    async def total_bytes_for_org(self) -> int:
        m = cast("Any", StoredFile)
        stmt = (
            select(func.coalesce(func.sum(m.size_bytes), 0))
            .select_from(StoredFile)
            # Not `status == "ready"`: a live presign is a reservation and
            # has to count, or concurrent presigns each pass against the
            # same free space. See app.modules.storage.quota.
            .where(m.org_id == self.org_id, counts_toward_quota(), m.deleted_at.is_(None))
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def count_files_for_org(self) -> int:
        m = cast("Any", StoredFile)
        stmt = (
            select(func.count())
            .select_from(StoredFile)
            .where(m.org_id == self.org_id, m.deleted_at.is_(None))
        )
        return (await self.session.execute(stmt)).scalar_one()

    async def list_cursor_filtered(
        self,
        after: UUID | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        *,
        bucket: str | None = None,
    ) -> tuple[list[StoredFile], UUID | None]:
        capped_limit = min(limit, MAX_PAGE_SIZE)
        m = cast("Any", StoredFile)
        stmt = select(StoredFile).where(m.org_id == self.org_id, m.deleted_at.is_(None))
        if bucket is not None:
            stmt = stmt.where(m.bucket == bucket)
        stmt = stmt.order_by(m.created_at.desc(), m.id.desc()).limit(capped_limit + 1)
        if after is not None:
            # get_cursor_anchor, not get(): a soft-deleted file is still a
            # valid position to page past, and an unresolvable cursor is an
            # error rather than a silent reset to page one.
            anchor = await self.get_cursor_anchor(after)
            stmt = stmt.where(tuple_(m.created_at, m.id) < (anchor.created_at, anchor.id))
        result = await self.session.execute(stmt)
        items = list(result.scalars().all())
        page = items[:capped_limit]
        next_cursor = page[-1].id if page and len(items) > capped_limit else None
        return page, next_cursor
