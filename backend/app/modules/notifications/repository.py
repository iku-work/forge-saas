"""
DB access for the notifications module (T-102): org+user-scoped Notification
reads/writes (plus the unread-count Redis cache), and user-scoped PushToken
device management.

Deliberately not a BaseRepository subclass, same reasoning as
app.modules.billing.repository: Notification has no `deleted_at` (T-101's
model docstring -- notifications are hard-deleted by a retention job, never
soft-deleted), so it doesn't satisfy BaseRepository's `_OrgScopedRecord`
shape. PushToken is looked up by its own unique token / by user_id, never by
org_id, like the auth module's SessionRepository.
"""

from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import CursorResult, delete, func, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache_keys
from app.core.exceptions import NotFoundError
from app.core.repository import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.models._mixins import utcnow
from app.modules.notifications.models import Notification, PushToken

_UNREAD_COUNT_CACHE_TTL_SECONDS = 30
_IDEMPOTENCY_WINDOW = timedelta(hours=24)


def _unread_count_cache_key(user_id: UUID) -> str:
    # T-240: key built in app.core.cache_keys like every other cache key.
    # The value stays a bare integer rather than one of app.core.cache's
    # versioned JSON envelopes -- there is no payload shape here that a
    # future change could invalidate.
    return cache_keys.notification_unread_count_key(user_id)


class NotificationRepository:
    def __init__(self, session: AsyncSession, org_id: UUID, redis: Redis) -> None:
        self.session = session
        self.org_id = org_id
        self._redis = redis

    async def get_or_raise(self, notification_id: UUID) -> Notification:
        m = cast("Any", Notification)
        stmt = select(Notification).where(m.id == notification_id, m.org_id == self.org_id)
        result = await self.session.execute(stmt)
        notification = result.scalar_one_or_none()
        if notification is None:
            raise NotFoundError("errors.notification_not_found", fallback="Notification not found")
        return notification

    async def create(self, **kwargs: Any) -> Notification:  # noqa: ANN401 -- see core.repository.BaseRepository.create
        org_override = kwargs.get("org_id")
        if org_override is not None and org_override != self.org_id:
            raise ValueError("create() cannot set org_id to a different organization")
        kwargs["org_id"] = self.org_id
        notification = Notification(**kwargs)
        self.session.add(notification)
        await self.session.flush()
        await self._invalidate_unread_count(notification.user_id)
        return notification

    async def find_recent_by_idempotency_key(
        self, user_id: UUID, idempotency_key: str
    ) -> Notification | None:
        m = cast("Any", Notification)
        cutoff = utcnow() - _IDEMPOTENCY_WINDOW
        stmt = select(Notification).where(
            m.org_id == self.org_id,
            m.user_id == user_id,
            m.idempotency_key == idempotency_key,
            m.created_at >= cutoff,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: UUID,
        after: UUID | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        *,
        unread_only: bool = False,
    ) -> tuple[list[Notification], UUID | None]:
        capped_limit = min(limit, MAX_PAGE_SIZE)
        m = cast("Any", Notification)
        stmt = select(Notification).where(m.org_id == self.org_id, m.user_id == user_id)
        if unread_only:
            stmt = stmt.where(m.read_at.is_(None))
        stmt = stmt.order_by(m.created_at.desc(), m.id.desc()).limit(capped_limit + 1)
        if after is not None:
            # Scoped to org_id + user_id, not get_or_raise(): an anchor that
            # belongs to another org or another user in this org is treated
            # as "no cursor" (mirrors app.modules.audit.repository's
            # list_cursor) rather than raising or leaking its position.
            anchor_stmt = select(Notification).where(
                m.id == after, m.org_id == self.org_id, m.user_id == user_id
            )
            anchor = (await self.session.execute(anchor_stmt)).scalar_one_or_none()
            if anchor is not None:
                stmt = stmt.where(tuple_(m.created_at, m.id) < (anchor.created_at, anchor.id))
        result = await self.session.execute(stmt)
        items = list(result.scalars().all())
        page = items[:capped_limit]
        # `page` guards against a non-positive capped_limit (e.g. limit=0
        # reaching here despite the router's Query(ge=1)): items can still be
        # non-empty (.limit(capped_limit + 1) == .limit(1) when capped_limit
        # is 0), but `page` is then empty -- page[-1] would raise IndexError.
        next_cursor = page[-1].id if page and len(items) > capped_limit else None
        return page, next_cursor

    async def mark_read(self, notification_id: UUID, user_id: UUID) -> Notification:
        notification = await self.get_or_raise(notification_id)
        if notification.user_id != user_id:
            # Same-org, different-user notification -- indistinguishable from
            # a missing one to the caller, same as tenancy's cross-org checks
            # (app.modules.tenancy.service.change_role).
            raise NotFoundError("errors.notification_not_found", fallback="Notification not found")
        notification.read_at = utcnow()
        await self.session.flush()
        await self._invalidate_unread_count(user_id)
        return notification

    async def mark_unread(self, notification_id: UUID, user_id: UUID) -> Notification:
        """The inverse of mark_read, so the web app's "Undo" can restore the
        state rather than only the appearance (F-009).

        Same ownership check and same not-found shape as mark_read -- an undo
        is an ordinary write by the same caller a moment later, and gets no
        special authority for being one.
        """
        notification = await self.get_or_raise(notification_id)
        if notification.user_id != user_id:
            raise NotFoundError("errors.notification_not_found", fallback="Notification not found")
        notification.read_at = None
        await self.session.flush()
        await self._invalidate_unread_count(user_id)
        return notification

    async def mark_all_read(self, user_id: UUID) -> int:
        m = cast("Any", Notification)
        stmt = (
            update(Notification)
            .where(m.org_id == self.org_id, m.user_id == user_id, m.read_at.is_(None))
            .values(read_at=utcnow())
        )
        result = await self.session.execute(stmt)
        await self._invalidate_unread_count(user_id)
        return cast("CursorResult[Any]", result).rowcount

    async def unread_count(self, user_id: UUID) -> int:
        cached = await self._redis.get(_unread_count_cache_key(user_id))
        if cached is not None:
            return int(cached)
        m = cast("Any", Notification)
        stmt = (
            select(func.count())
            .select_from(Notification)
            .where(m.org_id == self.org_id, m.user_id == user_id, m.read_at.is_(None))
        )
        count = (await self.session.execute(stmt)).scalar_one()
        await self._redis.set(
            _unread_count_cache_key(user_id), count, ex=_UNREAD_COUNT_CACHE_TTL_SECONDS
        )
        return count

    async def _invalidate_unread_count(self, user_id: UUID) -> None:
        await self._redis.delete(_unread_count_cache_key(user_id))

    @staticmethod
    async def prune_older_than(session: AsyncSession, cutoff: datetime) -> int:
        """T-219: the hard-delete retention job this class's own docstring
        (and T-101's model docstring) promised -- called by
        app.workers.scheduler.prune_old_notifications. A `@staticmethod`,
        not an instance method, because it purges across every org in one
        sweep; every other method here is deliberately scoped to a single
        org_id via __init__, which doesn't fit a global retention pass.
        """
        m = cast("Any", Notification)
        stmt = delete(Notification).where(m.created_at < cutoff)
        result = await session.execute(stmt)
        await session.flush()
        return cast("CursorResult[Any]", result).rowcount


class PushTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def register(
        self, user_id: UUID, token: str, platform: str, device_label: str | None = None
    ) -> PushToken:
        m = cast("Any", PushToken)
        stmt = select(PushToken).where(m.token == token)
        existing = (await self.session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            # Re-registering (token unchanged, or a previously deactivated
            # token Expo has since reissued to a new install) reactivates and
            # re-points it rather than creating a duplicate row, since `token`
            # is unique.
            existing.user_id = user_id
            existing.platform = platform
            existing.device_label = device_label
            existing.last_used_at = utcnow()
            existing.deleted_at = None
            await self.session.flush()
            return existing
        push_token = PushToken(
            user_id=user_id,
            token=token,
            platform=platform,
            device_label=device_label,
            last_used_at=utcnow(),
        )
        self.session.add(push_token)
        await self.session.flush()
        return push_token

    async def list_active_for_user(self, user_id: UUID) -> list[PushToken]:
        m = cast("Any", PushToken)
        stmt = select(PushToken).where(m.user_id == user_id, m.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def deactivate(self, token: str) -> None:
        m = cast("Any", PushToken)
        stmt = select(PushToken).where(m.token == token)
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is not None:
            row.deleted_at = utcnow()
            await self.session.flush()
