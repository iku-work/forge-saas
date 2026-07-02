"""
NotificationService (T-102): the only way any module creates a Notification.
send() resolves the recipient's per-type channel preferences (T-101.5),
persists the row, and fans out to whichever of email/push/in-app are enabled
-- email and push go through ARQ jobs (app.core.jobs) so a slow downstream
provider never blocks the caller; in-app publishes to the Redis channel
T-103's SSE endpoint subscribes to. All three are fire-and-forget: send()'s
own success only means the Notification row was written, not that any
channel actually delivered.
"""

from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import jobs
from app.core.exceptions import NotFoundError
from app.modules.audit.service import AuditService
from app.modules.auth.repository import UserRepository
from app.modules.notifications.models import Notification
from app.modules.notifications.preferences import channels_for
from app.modules.notifications.repository import NotificationRepository

# Public: app.modules.notifications.sse's SSE stream subscribes to this same
# channel name, so the prefix lives here once rather than being duplicated.
IN_APP_CHANNEL_PREFIX = "notif:user:"


class NotificationService:
    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self._session = session
        self._redis = redis
        self._users = UserRepository(session)
        self._audit = AuditService(session)

    async def send(  # noqa: PLR0913 -- matches the T-102 spec's exact param set
        self,
        *,
        user_id: UUID,
        org_id: UUID,
        type: str,  # noqa: A002 -- matches the T-102 spec's `type` param name
        title: str,
        body: str,
        action_url: str | None = None,
        idempotency_key: str | None = None,
    ) -> Notification:
        repo = NotificationRepository(self._session, org_id, self._redis)

        if idempotency_key is not None:
            existing = await repo.find_recent_by_idempotency_key(user_id, idempotency_key)
            if existing is not None:
                return existing

        user = await self._users.get_by_id(user_id)
        if user is None:
            raise NotFoundError("errors.user_not_found", fallback="User not found")

        channels = channels_for(user.notification_preferences, type)
        notification = await repo.create(
            user_id=user_id,
            type=type,
            title=title,
            body=body,
            action_url=action_url,
            channels=channels,
            idempotency_key=idempotency_key,
        )

        if "email" in channels:
            await jobs.enqueue_job(
                "send_email",
                to=user.email,
                template=f"notification_{type}",
                locale=user.locale,
                context={"title": title, "body": body, "action_url": action_url},
            )
        if "push" in channels:
            await jobs.enqueue_job("send_push", user_id=user_id, notification_id=notification.id)
        if "in_app" in channels:
            await self._redis.publish(
                f"{IN_APP_CHANNEL_PREFIX}{user_id}", notification.model_dump_json()
            )

        await self._audit.log(
            action="notification.sent",
            resource_type="notification",
            resource_id=str(notification.id),
            org_id=org_id,
            diff={"type": type, "channels": channels},
        )
        return notification
