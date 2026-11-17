"""DB access for feedback. Scoped by the sender on the way in; read across
every sender by the admin inbox only (FeedbackService says who may call it)."""

from typing import Any, cast
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InvalidCursorError
from app.core.repository import MAX_PAGE_SIZE
from app.models._mixins import utcnow
from app.modules.feedback.models import Feedback


class FeedbackRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        user_id: UUID,
        org_id: UUID | None,
        category: str,
        message: str,
        page: str | None,
    ) -> Feedback:
        row = Feedback(
            user_id=user_id, org_id=org_id, category=category, message=message, page=page
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_by_id(self, feedback_id: UUID) -> Feedback | None:
        m = cast("Any", Feedback)
        stmt = select(Feedback).where(m.id == feedback_id, m.deleted_at.is_(None))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_inbox(
        self, *, resolved: bool, after: UUID | None, limit: int
    ) -> tuple[list[Feedback], UUID | None]:
        capped = min(limit, MAX_PAGE_SIZE)
        m = cast("Any", Feedback)
        state = m.resolved_at.is_not(None) if resolved else m.resolved_at.is_(None)
        stmt = (
            select(Feedback)
            .where(state, m.deleted_at.is_(None))
            .order_by(m.created_at.desc(), m.id.desc())
            .limit(capped + 1)
        )
        if after is not None:
            anchor = await self.session.get(Feedback, after)
            if anchor is None:
                raise InvalidCursorError(
                    "errors.invalid_cursor",
                    fallback="This page cursor is no longer valid -- start from the first page",
                )
            stmt = stmt.where(tuple_(m.created_at, m.id) < (anchor.created_at, anchor.id))
        rows = list((await self.session.execute(stmt)).scalars().all())
        page = rows[:capped]
        return page, page[-1].id if len(rows) > capped else None

    async def set_resolved(self, row: Feedback, resolved_by: UUID | None) -> Feedback:
        row.resolved_at = utcnow() if resolved_by is not None else None
        row.resolved_by = resolved_by
        self.session.add(row)
        await self.session.flush()
        return row
