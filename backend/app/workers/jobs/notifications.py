"""
T-101.5: backfills app.modules.auth.models.User.notification_preferences for
any row still holding the column's empty-dict default -- accounts created
before this job first runs, since the JSONB column's `default=dict` is a
client-side SQLAlchemy default (applied on insert) rather than a server-side
one, and existing rows predate it entirely.

Wired into two places per agents.md: app.core.seed.seed() (every `make seed`)
and app.workers.main.WorkerSettings.functions, so ops can also enqueue it
once after deploying T-101.5 to production. Idempotent either way -- a user
already on DEFAULT_PREFERENCES (or a customised subset) no longer matches the
`== {}` WHERE clause, so re-running settles at zero rows updated.

T-102's send_push is the Expo-push delivery channel NotificationService.send()
enqueues when "push" is in a notification's resolved channels. It talks to
Expo's HTTP push API directly (httpx) rather than through a dedicated SDK --
one endpoint's worth of POST + response-parsing doesn't justify a new
dependency, the same "no abstraction ahead of need" call app.workers.jobs.email
makes for Resend.
"""

import logging
from typing import Any, cast
from uuid import UUID

import httpx
import sentry_sdk
from sqlalchemy import CursorResult, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import async_session_factory
from app.core.metrics import FORGE_PUSH_SENT_TOTAL
from app.core.tracing import trace_job
from app.models._mixins import utcnow
from app.modules.auth.models import User
from app.modules.notifications.models import Notification
from app.modules.notifications.preferences import DEFAULT_PREFERENCES
from app.modules.notifications.repository import PushTokenRepository

logger = logging.getLogger(__name__)

_EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
_EXPO_DEVICE_NOT_REGISTERED = "DeviceNotRegistered"


async def backfill_notification_preferences_for_session(session: AsyncSession) -> int:
    m = cast("Any", User)
    result = await session.execute(
        update(User)
        .where(m.notification_preferences == {})
        .values(notification_preferences=DEFAULT_PREFERENCES)
    )
    return cast("CursorResult[Any]", result).rowcount


@trace_job
async def backfill_notification_preferences(
    ctx: dict[str, Any],  # noqa: ARG001 -- ARQ calling convention
) -> None:
    async with async_session_factory() as session:
        updated = await backfill_notification_preferences_for_session(session)
        await session.commit()
    logger.info("backfill_notification_preferences: updated %d user(s)", updated)


def _expo_message(token: str, notification: Notification) -> dict[str, Any]:
    return {
        "to": token,
        "title": notification.title,
        "body": notification.body,
        "data": {"action_url": notification.action_url},
    }


@trace_job
async def send_push(
    ctx: dict[str, Any],  # noqa: ARG001 -- ARQ calling convention; registered in app.workers.main
    *,
    user_id: UUID,
    notification_id: UUID,
) -> None:
    async with async_session_factory() as session:
        notification = await session.get(Notification, notification_id)
        if notification is None:
            logger.info("send_push: notification_id=%s no longer exists, skipping", notification_id)
            return

        token_repo = PushTokenRepository(session)
        tokens = await token_repo.list_active_for_user(user_id)
        if not tokens:
            # User opted into push but has no registered device -- nothing to
            # do; email/in_app (if also enabled) already went out independently.
            return

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    _EXPO_PUSH_URL, json=[_expo_message(t.token, notification) for t in tokens]
                )
                response.raise_for_status()
                receipts = response.json().get("data", [])
        except httpx.HTTPError as exc:
            logger.warning("send_push: Expo push API request failed: %s", exc)
            sentry_sdk.capture_exception(exc)
            return

        sent_count = 0
        for push_token, receipt in zip(tokens, receipts, strict=False):
            if receipt.get("status") == "error":
                if receipt.get("details", {}).get("error") == _EXPO_DEVICE_NOT_REGISTERED:
                    await token_repo.deactivate(push_token.token)
                continue
            sent_count += 1
        FORGE_PUSH_SENT_TOTAL.inc(sent_count)

        notification.push_sent_at = utcnow()
        await session.commit()
