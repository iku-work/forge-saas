"""Feedback: a signed-in user sends a message; superadmins read it in the
admin Feedback inbox and mark it handled. Each new message also notifies
every superadmin (in the app, and by email unless they turned that off)."""

from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.module_loader import is_enabled
from app.modules.auth.models import User
from app.modules.auth.service import AuthService
from app.modules.feedback.models import Feedback
from app.modules.feedback.repository import FeedbackRepository
from app.modules.feedback.schemas import FeedbackCreateRequest
from app.modules.tenancy.service import TenancyService


class FeedbackService:
    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self._session = session
        self._redis = redis
        self._feedback = FeedbackRepository(session)

    async def submit(self, user: User, body: FeedbackCreateRequest) -> Feedback:
        if body.org_id is not None:
            # Refuses an org the sender is not in: nobody files a message
            # under someone else's org.
            await TenancyService(self._session).get_membership_or_raise(body.org_id, user.id)
        row = await self._feedback.create(
            user_id=user.id,
            org_id=body.org_id,
            category=body.category,
            message=body.message.strip(),
            page=body.page,
        )
        if is_enabled("notifications"):
            from app.modules.notifications.service import NotificationService  # noqa: PLC0415

            admins = [i for i in await AuthService(self._session).superadmin_ids() if i != user.id]
            # No org: a superadmin is usually not a member of the sender's
            # org, and the inbox hides notifications from orgs you are not in.
            await NotificationService(self._session, self._redis).send_many(
                user_ids=admins,
                org_id=None,
                type="feedback_received",
                params={"sender": user.email},
                action_url="/admin/feedback",
            )
        return row

    async def list_inbox(
        self, *, resolved: bool, after: UUID | None, limit: int
    ) -> tuple[list[Feedback], UUID | None]:
        return await self._feedback.list_inbox(resolved=resolved, after=after, limit=limit)

    async def set_resolved(self, feedback_id: UUID, *, actor: User, resolved: bool) -> Feedback:
        row = await self._feedback.get_by_id(feedback_id)
        if row is None:
            raise NotFoundError("errors.feedback_not_found", fallback="Feedback not found")
        return await self._feedback.set_resolved(row, actor.id if resolved else None)
