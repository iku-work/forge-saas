"""
DB access for AuditEvent (T-028). Deliberately not a BaseRepository subclass
and deliberately has no update()/soft_delete(): AuditEvent has no updated_at
or deleted_at to bump or filter on, and §0.3 immutability means there must be
no code path that can mutate or remove a row once written.
"""

from typing import Any, cast
from uuid import UUID

from sqlalchemy import exists, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.repository import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.modules.audit.models import AuditEvent


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, **kwargs: Any) -> AuditEvent:  # noqa: ANN401 — forwarded to AuditEvent(**kwargs)
        event = AuditEvent(**kwargs)
        self.session.add(event)
        await self.session.flush()
        return event

    async def exists(self, *, action: str, resource_id: str) -> bool:
        # Idempotency check for at-least-once background steps (T-130's
        # per-day dunning email) that must not re-send on a retried run --
        # the audit log itself doubles as the "already did this" marker.
        m = cast("Any", AuditEvent)
        stmt = select(exists().where(m.action == action, m.resource_id == resource_id))
        result = await self.session.execute(stmt)
        return bool(result.scalar_one())

    async def list_cursor(
        self, org_id: UUID, after: UUID | None = None, limit: int = DEFAULT_PAGE_SIZE
    ) -> tuple[list[AuditEvent], UUID | None]:
        # Cursor pagination per §0.3, ordered on (ts DESC, id DESC) — mirrors
        # BaseRepository.list_cursor's (created_at, id) cursor, using this
        # model's `ts` column instead of `created_at`.
        capped_limit = min(limit, MAX_PAGE_SIZE)
        m = cast("Any", AuditEvent)
        stmt = (
            select(AuditEvent)
            .where(m.org_id == org_id)
            .order_by(m.ts.desc(), m.id.desc())
            .limit(capped_limit + 1)
        )
        if after is not None:
            # Scoped to org_id, not a bare session.get(): an anchor id that
            # belongs to a different org must be treated as "no cursor" (same
            # as BaseRepository.list_cursor, whose org-scoped get() gives it
            # this for free) rather than leaking that org's (ts, id) into the
            # WHERE clause below.
            anchor_stmt = select(AuditEvent).where(m.id == after, m.org_id == org_id)
            anchor = (await self.session.execute(anchor_stmt)).scalar_one_or_none()
            if anchor is not None:
                stmt = stmt.where(tuple_(m.ts, m.id) < (anchor.ts, anchor.id))
        result = await self.session.execute(stmt)
        items = list(result.scalars().all())
        page = items[:capped_limit]
        next_cursor = page[-1].id if len(items) > capped_limit else None
        return page, next_cursor

    async def count_by_actor(self, actor_id: UUID) -> int:
        # T-141: backs the admin user-detail "audit summary" total count.
        m = cast("Any", AuditEvent)
        stmt = select(func.count()).select_from(AuditEvent).where(m.actor_id == actor_id)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def list_recent_by_actor(
        self, actor_id: UUID, limit: int = DEFAULT_PAGE_SIZE
    ) -> list[AuditEvent]:
        # T-141: a flat "most recent N" list for the admin user-detail
        # summary, not full cursor pagination -- support staff want a quick
        # glance, not to page through a user's entire history here.
        m = cast("Any", AuditEvent)
        stmt = (
            select(AuditEvent)
            .where(m.actor_id == actor_id)
            .order_by(m.ts.desc(), m.id.desc())
            .limit(min(limit, MAX_PAGE_SIZE))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
