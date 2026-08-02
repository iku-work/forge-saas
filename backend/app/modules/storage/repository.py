"""
DB access for the storage module (T-112): org-scoped StoredFile reads/writes,
plus the 60s-cached `total_bytes_for_org` quota read StorageService.presign_upload
checks on every request. Cached the same way NotificationRepository caches
unread_count (app.modules.notifications.repository) -- a Redis GET/SET around
the query, with the cache key invalidated explicitly by the service after any
write that changes which rows count toward the total (a file finishing
upload, or being deleted), rather than on a TTL alone.

Adds its own list_cursor-shaped method (list_cursor_filtered) instead of
using BaseRepository.list_cursor directly, since the spec's `GET /files`
needs an optional `bucket` filter BaseRepository's generic version has no way
to express -- same reasoning as NotificationRepository.list_for_user's own
unread_only filter.
"""

from typing import Any, cast
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache_keys
from app.core.repository import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, BaseRepository
from app.modules.storage.models import StoredFile

_TOTAL_BYTES_CACHE_TTL_SECONDS = 60


def _total_bytes_cache_key(org_id: UUID) -> str:
    # T-240: see NotificationRepository's own key helper -- the key comes
    # from app.core.cache_keys, the value stays a bare integer.
    return cache_keys.storage_total_bytes_key(org_id)


class StoredFileRepository(BaseRepository[StoredFile]):
    model = StoredFile

    def __init__(self, session: AsyncSession, org_id: UUID, redis: Redis) -> None:
        super().__init__(session, org_id)
        self._redis = redis

    async def total_bytes_for_org(self) -> int:
        cached = await self._redis.get(_total_bytes_cache_key(self.org_id))
        if cached is not None:
            return int(cached)
        m = cast("Any", StoredFile)
        stmt = (
            select(func.coalesce(func.sum(m.size_bytes), 0))
            .select_from(StoredFile)
            .where(m.org_id == self.org_id, m.status == "ready", m.deleted_at.is_(None))
        )
        total = (await self.session.execute(stmt)).scalar_one()
        await self._redis.set(
            _total_bytes_cache_key(self.org_id), total, ex=_TOTAL_BYTES_CACHE_TTL_SECONDS
        )
        return int(total)

    async def count_files_for_org(self) -> int:
        m = cast("Any", StoredFile)
        stmt = (
            select(func.count())
            .select_from(StoredFile)
            .where(m.org_id == self.org_id, m.deleted_at.is_(None))
        )
        return (await self.session.execute(stmt)).scalar_one()

    async def invalidate_total_bytes_cache(self) -> None:
        await self._redis.delete(_total_bytes_cache_key(self.org_id))

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
            anchor = await self.get(after)
            if anchor is not None:
                stmt = stmt.where(tuple_(m.created_at, m.id) < (anchor.created_at, anchor.id))
        result = await self.session.execute(stmt)
        items = list(result.scalars().all())
        page = items[:capped_limit]
        next_cursor = page[-1].id if page and len(items) > capped_limit else None
        return page, next_cursor
