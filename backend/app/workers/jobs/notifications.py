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

T-206: the actual POST to Expo's push API (`_post_to_expo`) goes through
app.core.resilience's `@resilient("expo_push")`. Both a transient failure
that survives every retry and an already-open "expo_push" breaker are
treated identically by send_push -- logged + reported to Sentry, no
re-queue -- per T-206's own degraded-mode contract ("Expo push down -- push
dropped after retries; in-app + email channels unaffected"), unlike Resend's
or the virus scanner's re-queue-via-arq.worker.Retry behavior: a missed push
has no user-facing recovery path worth delaying the rest of the queue for.

T-234: the message text is re-rendered here (see _localise) from the
notification's type + params in the recipient's locale *as of this job's
execution*, rather than reusing the strings NotificationService.send() stored
when the row was created. The two only differ when the user changed language
in between, but "in between" can be a week for a deferred dunning step, and
the language the push arrives in should be the one they have now.
"""

import logging
from typing import Any, cast
from uuid import UUID

import httpx
import sentry_sdk
from sqlalchemy import CursorResult, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import async_session_factory
from app.core.exceptions import ExternalServiceUnavailableError
from app.core.i18n import get_default_locale
from app.core.metrics import FORGE_PUSH_SENT_TOTAL
from app.core.resilience import resilient
from app.core.tracing import trace_job
from app.models._mixins import utcnow
from app.modules.auth.models import User
from app.modules.notifications import messages
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


def _localise(notification: Notification, user: User | None) -> tuple[str, str, str]:
    """(title, body, locale) for this push, resolved at *job execution* time.

    T-234: the row's stored title/body were rendered when the notification
    was created, which can be minutes (a busy queue) or days (a deferred
    dunning step) before this job runs -- long enough for the recipient to
    have switched languages in between. Re-rendering from the type + params
    means the push that actually lands is in the locale the user has now.

    Falls back to the stored pair when the user row is gone (a GDPR purge
    racing the queue) or the type has no catalog entry, which is what rows
    written before T-234's migration look like: `params` is `{}` and their
    title/body are the English literals their caller baked in. A missing
    catalog entry is logged rather than raised -- a failed ARQ job would not
    make the push any more localised.
    """
    if user is None:
        return notification.title, notification.body, get_default_locale()
    locale = user.locale
    try:
        title, body = messages.render(notification.type, locale, notification.params)
    except KeyError:
        logger.warning(
            "send_push: no message catalog entry for type=%r, using the stored strings",
            notification.type,
        )
        return notification.title, notification.body, locale
    return title, body, locale


def _expo_message(
    token: str, *, title: str, body: str, action_url: str | None, locale: str
) -> dict[str, Any]:
    return {
        "to": token,
        "title": title,
        "body": body,
        # `locale` (T-234) tells the mobile client which language the strings
        # it just received are in, so anything it renders itself around them
        # matches rather than following the device locale independently.
        "data": {"action_url": action_url, "locale": locale},
    }


@resilient("expo_push")
async def _post_to_expo(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(_EXPO_PUSH_URL, json=messages)
        response.raise_for_status()
        result: list[dict[str, Any]] = response.json().get("data", [])
        return result


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

        title, body, locale = _localise(notification, await session.get(User, user_id))

        try:
            receipts = await _post_to_expo(
                [
                    _expo_message(
                        token.token,
                        title=title,
                        body=body,
                        action_url=notification.action_url,
                        locale=locale,
                    )
                    for token in tokens
                ]
            )
        except (httpx.HTTPError, ExternalServiceUnavailableError) as exc:
            # Dropped, not re-queued -- see module docstring.
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
