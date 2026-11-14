"""
DB access for the notifications module (T-102): user-scoped Notification
reads/writes (plus the unread-count Redis cache), and user-scoped PushToken
device management.

Deliberately not a BaseRepository subclass, same reasoning as
app.modules.billing.repository: Notification has no `deleted_at` (T-101's
model docstring -- notifications are hard-deleted by a retention job, never
soft-deleted), so it doesn't satisfy BaseRepository's `_OrgScopedRecord`
shape. Both are looked up by user_id, never by org_id, like the auth module's
SessionRepository.
"""

from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import (
    ColumnElement,
    CursorResult,
    and_,
    delete,
    func,
    or_,
    select,
    tuple_,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache_keys
from app.core.exceptions import InvalidCursorError, NotFoundError
from app.core.repository import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.models._mixins import utcnow
from app.modules.notifications.models import Notification, PushToken
from app.modules.tenancy.models import Membership, Organization

_UNREAD_COUNT_CACHE_TTL_SECONDS = 30
_IDEMPOTENCY_WINDOW = timedelta(hours=24)


def _unread_count_cache_key(user_id: UUID) -> str:
    # T-240: key built in app.core.cache_keys like every other cache key.
    # The value stays a bare integer rather than one of app.core.cache's
    # versioned JSON envelopes -- there is no payload shape here that a
    # future change could invalidate.
    return cache_keys.notification_unread_count_key(user_id)


class NotificationRepository:
    """One recipient's notifications. §0.2's user-scoped shape: `user_id` is
    required and every query filters on it, the way BaseRepository filters
    on org_id.

    Reads go through _visible(): a notification from an org the user has left
    (or that was deleted) disappears with their access to it.
    """

    def __init__(self, session: AsyncSession, user_id: UUID, redis: Redis) -> None:
        self.session = session
        self.user_id = user_id
        self._redis = redis

    def _visible(self) -> ColumnElement[bool]:
        m = cast("Any", Notification)
        mem = cast("Any", Membership)
        org = cast("Any", Organization)
        still_a_member = (
            select(mem.id)
            .join(Organization, org.id == mem.org_id)
            .where(
                mem.user_id == self.user_id,
                mem.org_id == m.org_id,
                org.deleted_at.is_(None),
            )
            .exists()
        )
        return and_(m.user_id == self.user_id, or_(m.org_id.is_(None), still_a_member))

    async def get_or_raise(self, notification_id: UUID) -> Notification:
        # Another user's notification, or one from an org this user has left,
        # is indistinguishable from a missing one.
        m = cast("Any", Notification)
        stmt = select(Notification).where(m.id == notification_id, self._visible())
        result = await self.session.execute(stmt)
        notification = result.scalar_one_or_none()
        if notification is None:
            raise NotFoundError("errors.notification_not_found", fallback="Notification not found")
        return notification

    async def create(self, **kwargs: Any) -> Notification:  # noqa: ANN401 -- see core.repository.BaseRepository.create
        user_override = kwargs.get("user_id")
        if user_override is not None and user_override != self.user_id:
            raise ValueError("create() cannot set user_id to a different user")
        kwargs["user_id"] = self.user_id
        notification = Notification(**kwargs)
        self.session.add(notification)
        await self.session.flush()
        await self._invalidate_unread_count()
        return notification

    async def find_recent_by_idempotency_key(
        self, org_id: UUID | None, idempotency_key: str
    ) -> Notification | None:
        m = cast("Any", Notification)
        cutoff = utcnow() - _IDEMPOTENCY_WINDOW
        stmt = select(Notification).where(
            m.user_id == self.user_id,
            m.idempotency_key == idempotency_key,
            m.org_id.is_not_distinct_from(org_id),
            m.created_at >= cutoff,
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def list_page(
        self,
        after: UUID | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        *,
        unread_only: bool = False,
    ) -> tuple[list[Notification], UUID | None]:
        capped_limit = min(limit, MAX_PAGE_SIZE)
        m = cast("Any", Notification)
        stmt = select(Notification).where(self._visible())
        if unread_only:
            stmt = stmt.where(m.read_at.is_(None))
        stmt = stmt.order_by(m.created_at.desc(), m.id.desc()).limit(capped_limit + 1)
        if after is not None:
            # Through _visible(), not a bare id lookup: another user's anchor
            # must not leak its position. It is refused rather than ignored --
            # ignoring it returned page one, which the notification feed
            # appends as duplicates.
            anchor_stmt = select(Notification).where(m.id == after, self._visible())
            anchor = (await self.session.execute(anchor_stmt)).scalar_one_or_none()
            if anchor is None:
                raise InvalidCursorError(
                    "errors.invalid_cursor",
                    fallback=("This page cursor is no longer valid -- start from the first page"),
                )
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

    async def mark_read(self, notification_id: UUID) -> Notification:
        notification = await self.get_or_raise(notification_id)
        notification.read_at = utcnow()
        await self.session.flush()
        await self._invalidate_unread_count()
        return notification

    async def mark_unread(self, notification_id: UUID) -> Notification:
        """The inverse of mark_read, so the web app's "Undo" can restore the
        state rather than only the appearance (F-009).

        Same visibility rule and same not-found shape as mark_read -- an undo
        is an ordinary write by the same caller a moment later, and gets no
        special authority for being one.
        """
        notification = await self.get_or_raise(notification_id)
        notification.read_at = None
        await self.session.flush()
        await self._invalidate_unread_count()
        return notification

    async def mark_all_read(self) -> int:
        m = cast("Any", Notification)
        stmt = (
            update(Notification)
            .where(self._visible(), m.read_at.is_(None))
            .values(read_at=utcnow())
        )
        result = await self.session.execute(stmt)
        await self._invalidate_unread_count()
        return cast("CursorResult[Any]", result).rowcount

    async def unread_count(self) -> int:
        # ponytail: leaving an org doesn't clear this cache, so for up to its
        # 30 s TTL the badge can still count that org's unread items. Clear it
        # from tenancy's membership paths if that ever matters.
        cached = await self._redis.get(_unread_count_cache_key(self.user_id))
        if cached is not None:
            return int(cached)
        m = cast("Any", Notification)
        stmt = (
            select(func.count())
            .select_from(Notification)
            .where(self._visible(), m.read_at.is_(None))
        )
        count = (await self.session.execute(stmt)).scalar_one()
        await self._redis.set(
            _unread_count_cache_key(self.user_id), count, ex=_UNREAD_COUNT_CACHE_TTL_SECONDS
        )
        return count

    async def _invalidate_unread_count(self) -> None:
        await self._redis.delete(_unread_count_cache_key(self.user_id))

    @staticmethod
    async def prune_older_than(session: AsyncSession, cutoff: datetime) -> int:
        """T-219: the hard-delete retention job this class's own docstring
        (and T-101's model docstring) promised -- called by
        app.workers.scheduler.prune_old_notifications. A `@staticmethod`,
        not an instance method, because it purges across every user in one
        sweep; every other method here is deliberately scoped to a single
        user_id via __init__, which doesn't fit a global retention pass.
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
