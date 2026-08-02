"""
NotificationService (T-102): the only way any module creates a Notification.
send() resolves the recipient's per-type channel preferences (T-101.5),
persists the row, and fans out to whichever of email/push/in-app are enabled
-- email and push go through ARQ jobs (app.core.jobs) so a slow downstream
provider never blocks the caller; in-app publishes to the Redis channel
T-103's SSE endpoint subscribes to. All three are fire-and-forget: send()'s
own success only means the Notification row was written, not that any
channel actually delivered.

T-234: send() takes a `type` plus `params`, never a `title`/`body` string.
Everything a recipient reads is looked up here, once per recipient, from that
recipient's own `User.locale` (app.modules.notifications.messages) -- not
from the sender's locale, not from request.state.locale, and not before a
fan-out loop. send_many() exists so a multi-recipient event physically cannot
be rendered once and reused: it is a loop over send(), and each iteration
re-resolves locale, preferences and name from its own recipient's row.
"""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import jobs
from app.core.exceptions import NotFoundError
from app.core.urls import frontend_url
from app.modules.audit.service import AuditService
from app.modules.auth.models import User
from app.modules.auth.repository import UserRepository
from app.modules.notifications import messages
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

    async def send(  # noqa: PLR0913 -- matches the T-102 spec's param set (title/body replaced by params per T-234)
        self,
        *,
        user_id: UUID,
        org_id: UUID,
        type: str,  # noqa: A002 -- matches the T-102 spec's `type` param name
        params: dict[str, Any] | None = None,
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

        message_params = dict(params or {})
        # The recipient's locale, read inside the per-recipient path. Every
        # channel below renders from this, and send_push re-reads it at job
        # execution time in case it changed in between (T-234).
        locale = user.locale
        title, body = messages.render(type, locale, message_params)

        channels = channels_for(user.notification_preferences, type)
        notification = await repo.create(
            user_id=user_id,
            type=type,
            title=title,
            body=body,
            params=message_params,
            action_url=action_url,
            channels=channels,
            idempotency_key=idempotency_key,
        )

        if "email" in channels:
            await self._enqueue_email(user, notification)
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
            diff={"type": type, "channels": channels, "locale": locale},
        )
        return notification

    async def send_many(  # noqa: PLR0913 -- same param set as send(), with user_ids in place of user_id
        self,
        *,
        user_ids: Sequence[UUID],
        org_id: UUID,
        type: str,  # noqa: A002 -- see send()
        params: dict[str, Any] | None = None,
        action_url: str | None = None,
        idempotency_key: str | None = None,
    ) -> list[Notification]:
        """Fan one event out to several recipients, one Notification each.

        Deliberately a plain loop over send() rather than a batched insert
        with the message rendered once above it: rendering above the loop is
        precisely the T-234 bug this method exists to make unwriteable -- the
        first recipient's language would reach everyone. `idempotency_key` is
        safe to share across recipients; the repository scopes its dedup
        lookup to (org_id, user_id, key).
        """
        return [
            await self.send(
                user_id=user_id,
                org_id=org_id,
                type=type,
                params=params,
                action_url=action_url,
                idempotency_key=idempotency_key,
            )
            for user_id in user_ids
        ]

    async def _enqueue_email(self, user: User, notification: Notification) -> None:
        template = messages.email_template_for(notification.type)
        # The row stores action_url as a locale-less app path; each channel
        # absolutises it for its own recipient. An email's link has to carry
        # the locale segment (T-233) or a Finnish reader lands on the SPA's
        # compatibility redirect with no locale in the URL at all.
        link = (
            frontend_url(notification.action_url, user.locale)
            if notification.action_url is not None
            else None
        )
        if template == messages.GENERIC_EMAIL_TEMPLATE:
            # The generic template renders strings rather than looking them
            # up, so it gets the already-localised pair and the notification's
            # own title as the subject (there is no one fixed subject for
            # every type sharing this template).
            context: dict[str, Any] = {
                "user_name": user.full_name,
                "title": notification.title,
                "body": notification.body,
                "action_url": link,
            }
            subject: str | None = notification.title
        else:
            # A rich template looks its own strings up from `locale`; params
            # are its props verbatim, plus the CTA link under whichever prop
            # name that template calls it.
            context = {
                "user_name": user.full_name,
                **notification.params,
                messages.RICH_EMAIL_URL_PROP[notification.type]: link,
            }
            subject = None
        await jobs.enqueue_job(
            "send_email",
            to=user.email,
            template=template,
            locale=user.locale,
            subject=subject,
            context=context,
        )
