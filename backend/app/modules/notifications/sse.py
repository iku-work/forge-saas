"""
HTTP + SSE layer for the notifications module (T-103): the one-way real-time
push NotificationService.send() (T-102) feeds via Redis pub/sub, plus the
REST surface for listing/reading notifications and editing delivery
preferences. Named sse.py rather than router.py because the live stream is
the reason this file exists -- same one-extra-file-per-concern pattern as
app.modules.auth.oauth/totp sitting next to auth's own router.py.

The stream endpoint subscribes per *user*, not per org+user
(app.modules.notifications.service.IN_APP_CHANNEL_PREFIX has no org segment)
-- one open tab gets every org's notifications for that user, mirroring how
a single browser session can hold membership in several orgs at once.

Concurrency, not request-rate, is what needs capping on a connection that
stays open indefinitely, so neither this endpoint nor _event_stream go
through app.middleware.rate_limit's slowapi decorators the way the REST
endpoints below do. Instead two Redis sorted sets (per-user, and a global one
bounded by settings.SSE_MAX_CONNECTIONS) gate *opening* a connection --
checked before the StreamingResponse/generator is even constructed, so a
rejection returns a normal envelope response rather than ever entering the
stream.

Sorted sets rather than counters, scored by when each connection last checked
in, because a counter can only be corrected by the code that incremented it.
Slots were previously incremented in the route handler and decremented in the
generator's `finally`, which does not run when the process dies -- so every
deploy with a stream open left a permanently elevated count on an unexpiring
key, and five of those locked that user out of their own notifications with
no cure but flushing Redis. Scoring by last-seen means an abandoned slot is
reclaimed on its own; see _STALE_AFTER_SECONDS.

Pinging on a per-message timeout (pubsub.get_message(..., timeout=30)
returning None) rather than a separate clock task keeps the implementation
to one coroutine: a real event always preempts the timer, and 30s of silence
always produces a ": ping" keepalive comment, satisfying both halves of the
spec's requirement with nothing extra to clean up on disconnect.
"""

import time
from collections.abc import AsyncGenerator
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.dependencies import (
    get_current_org,
    get_current_user,
    get_redis,
    invalidate_user_cache,
    reject_during_impersonation,
)
from app.core.envelope import Envelope, Meta
from app.core.exceptions import RateLimitError
from app.middleware.locale import get_locale
from app.middleware.rate_limit import api_limit
from app.modules.auth.models import User
from app.modules.auth.service import AuthService
from app.modules.notifications import messages
from app.modules.notifications.models import Notification
from app.modules.notifications.repository import NotificationRepository, PushTokenRepository
from app.modules.notifications.schemas import (
    MarkAllReadResponse,
    NotificationPreferencesUpdate,
    NotificationPublic,
    PushTokenPublic,
    PushTokenRegisterRequest,
    UnreadCountResponse,
)
from app.modules.notifications.service import IN_APP_CHANNEL_PREFIX
from app.modules.tenancy.models import Membership, Organization

router = APIRouter(tags=["notifications"])

_PING_INTERVAL_SECONDS = 30.0
_PER_USER_CONNECTION_LIMIT = 5
# `:v2:` is load-bearing. These keys were plain counters until the sorted-set
# rewrite, and Redis refuses a ZCARD against a string with WRONGTYPE -- so
# deploying that change over live keys broke every stream request until
# somebody flushed Redis by hand. A change whose entire purpose is surviving a
# deploy could not survive its own. Versioning the name sidesteps the type
# clash completely: the old keys are simply never read again, and the new ones
# carry a TTL so this scheme leaves nothing behind either.
_TOTAL_CONNECTIONS_KEY = "notif:sse:conns:v2:total"

# How long a connection may go without checking in before its slot is
# reclaimed. Three ping intervals: two may be missed to a slow network or a
# paused event loop without a live stream losing its place.
#
# This exists because the previous design could not survive a restart. Slots
# were plain counters incremented in the route handler and decremented in the
# generator's `finally` -- so a process that died with a stream open left the
# count permanently high, with nothing to bring it back down and no expiry on
# the key. Five of those and that user could never open a stream again; the
# only cure was flushing Redis by hand. It is not a rare event either: every
# deploy kills the process with streams open, by definition.
#
# Sorted sets scored by last-seen fix that by construction. A slot is held
# only while something keeps saying it is there, so a slot whose process is
# gone is reclaimed after this long, and the count is a description of what is
# connected rather than a tally of what once connected.
_STALE_AFTER_SECONDS = _PING_INTERVAL_SECONDS * 3
# Comfortably past _STALE_AFTER_SECONDS: pruning already handles correctness,
# and this is only so an abandoned key does not sit in Redis forever.
_KEY_TTL_SECONDS = int(_STALE_AFTER_SECONDS * 4)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid4())


def _public(notification: Notification, locale: str) -> NotificationPublic:
    """T-234: re-renders the row's message in the *reader's* current locale.

    The stored title/body are the send-time rendering (correct for the push
    and email channels, which leave at that moment), but the feed is read
    again and again afterwards -- a user who switches language would
    otherwise scroll a history frozen in the language they used back then.
    Rows the message catalog can't render -- a type with no entry, or one of
    the pre-T-234 rows whose `params` is `{}` and whose title/body are the
    English literals their caller baked in -- keep what they have.
    """
    public = NotificationPublic.model_validate(notification)
    try:
        title, body = messages.render(notification.type, locale, notification.params)
    except KeyError:
        return public
    return public.model_copy(update={"title": title, "body": body})


def _user_connections_key(user_id: UUID) -> str:
    return f"notif:sse:conns:v2:{user_id}"


def _total_member(user_id: UUID, connection_id: str) -> str:
    return f"{user_id}:{connection_id}"


async def _prune_stale(redis: Redis, user_id: UUID, now: float) -> None:
    cutoff = now - _STALE_AFTER_SECONDS
    await redis.zremrangebyscore(_user_connections_key(user_id), "-inf", cutoff)
    await redis.zremrangebyscore(_TOTAL_CONNECTIONS_KEY, "-inf", cutoff)


async def _acquire_connection_slot(redis: Redis, user_id: UUID, connection_id: str) -> None:
    now = time.time()
    await _prune_stale(redis, user_id, now)

    if await redis.zcard(_user_connections_key(user_id)) >= _PER_USER_CONNECTION_LIMIT:
        raise RateLimitError(
            "errors.too_many_notification_streams",
            fallback="Too many open notification streams for this account",
        )
    if await redis.zcard(_TOTAL_CONNECTIONS_KEY) >= get_settings().SSE_MAX_CONNECTIONS:
        raise RateLimitError(
            "errors.notification_stream_capacity_exceeded",
            fallback="Notification stream capacity exceeded, try again shortly",
        )

    await _touch_connection_slot(redis, user_id, connection_id, now=now)


async def _touch_connection_slot(
    redis: Redis, user_id: UUID, connection_id: str, *, now: float | None = None
) -> None:
    """Records this connection as alive, as of now.

    Called on every ping, which is what turns the sets below from a tally
    that only ever grows into a picture of what is actually connected.
    """
    timestamp = time.time() if now is None else now
    user_key = _user_connections_key(user_id)
    await redis.zadd(user_key, {connection_id: timestamp})
    await redis.zadd(_TOTAL_CONNECTIONS_KEY, {_total_member(user_id, connection_id): timestamp})
    # So a user who never comes back stops costing a key at all. Refreshed on
    # every ping, so it can only fire once every connection really is gone.
    await redis.expire(user_key, _KEY_TTL_SECONDS)


async def _release_connection_slot(redis: Redis, user_id: UUID, connection_id: str) -> None:
    await redis.zrem(_user_connections_key(user_id), connection_id)
    await redis.zrem(_TOTAL_CONNECTIONS_KEY, _total_member(user_id, connection_id))


async def _event_stream(
    redis: Redis, user_id: UUID, connection_id: str
) -> AsyncGenerator[str, None]:
    channel = f"{IN_APP_CHANNEL_PREFIX}{user_id}"
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)
    # subscribe() always produces exactly one subscribe-confirmation message
    # as the next thing readable on this connection (Redis only starts
    # relaying real publishes once the subscription is registered, so this
    # can never race a real event) -- drained here so the client's first read
    # is never a spurious ping caused by get_message() consuming that
    # confirmation on the first loop iteration below.
    await pubsub.get_message(timeout=1.0)
    try:
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=_PING_INTERVAL_SECONDS
            )
            if message is None:
                # The ping doubles as the liveness check-in -- a stream that
                # is still yielding to a client is a stream whose slot should
                # not be reclaimed. Doing it here rather than on a timer means
                # a wedged generator stops checking in, which is the whole
                # point.
                await _touch_connection_slot(redis, user_id, connection_id)
                yield ": ping\n\n"
                continue
            data = message["data"]
            if isinstance(data, bytes):
                data = data.decode()
            yield f"event: notification\ndata: {data}\n\n"
    finally:
        # Covers both a normal generator close and the asyncio.CancelledError
        # raised mid-`await` when the client disconnects (a closed tab or a
        # dropped connection) -- the only way this stream ever ends.
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()  # type: ignore[no-untyped-call]  # redis-py's PubSub.aclose ships with no return annotation
        await _release_connection_slot(redis, user_id, connection_id)


@router.get("/notifications/stream")
async def stream_notifications(
    user: Annotated[User, Depends(get_current_user)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> StreamingResponse:
    # Identifies this one stream in the sets above, so a slot belongs to a
    # connection rather than to a number that anybody may decrement.
    connection_id = str(uuid4())
    await _acquire_connection_slot(redis, user.id, connection_id)
    return StreamingResponse(
        _event_stream(redis, user.id, connection_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/notifications")
@api_limit
async def list_notifications(  # noqa: PLR0913 -- cursor pagination (§0.3) needs after+limit on top of the usual params
    request: Request,
    org_and_membership: Annotated[tuple[Organization, Membership], Depends(get_current_org)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
    locale: Annotated[str, Depends(get_locale)],
    after: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    unread_only: Annotated[bool, Query()] = False,
) -> Envelope[list[NotificationPublic]]:
    org, membership = org_and_membership
    repo = NotificationRepository(session, org.id, redis)
    notifications, next_cursor = await repo.list_for_user(
        membership.user_id, after, limit, unread_only=unread_only
    )
    data = [_public(n, locale) for n in notifications]
    meta = Meta(
        request_id=_request_id(request),
        next_cursor=str(next_cursor) if next_cursor is not None else None,
    )
    return Envelope(data=data, meta=meta)


@router.patch(
    "/notifications/{notification_id}/read",
    dependencies=[Depends(reject_during_impersonation("notification_read_state"))],
)
@api_limit
async def mark_notification_read(  # noqa: PLR0913 -- the usual router param set plus the reader's locale (T-234)
    request: Request,
    notification_id: UUID,
    org_and_membership: Annotated[tuple[Organization, Membership], Depends(get_current_org)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
    locale: Annotated[str, Depends(get_locale)],
) -> Envelope[NotificationPublic]:
    org, membership = org_and_membership
    repo = NotificationRepository(session, org.id, redis)
    notification = await repo.mark_read(notification_id, membership.user_id)
    await session.commit()
    return Envelope(
        data=_public(notification, locale),
        meta=Meta(request_id=_request_id(request)),
    )


@router.patch(
    "/notifications/{notification_id}/unread",
    dependencies=[Depends(reject_during_impersonation("notification_read_state"))],
)
@api_limit
async def mark_notification_unread(  # noqa: PLR0913 -- the usual router param set plus the reader's locale (T-234)
    request: Request,
    notification_id: UUID,
    org_and_membership: Annotated[tuple[Organization, Membership], Depends(get_current_org)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
    locale: Annotated[str, Depends(get_locale)],
) -> Envelope[NotificationPublic]:
    """The inverse of the endpoint above, so "Mark as read" can offer an Undo
    that restores the state and not just the row's appearance (F-009).

    Carries the same impersonation guard as mark-read: read state is the
    user's own, and a superadmin acting as them must not quietly change what
    they have and have not seen -- in either direction.
    """
    org, membership = org_and_membership
    repo = NotificationRepository(session, org.id, redis)
    notification = await repo.mark_unread(notification_id, membership.user_id)
    await session.commit()
    return Envelope(
        data=_public(notification, locale),
        meta=Meta(request_id=_request_id(request)),
    )


@router.post(
    "/notifications/read-all",
    dependencies=[Depends(reject_during_impersonation("notification_read_state"))],
)
@api_limit
async def mark_all_notifications_read(
    request: Request,
    org_and_membership: Annotated[tuple[Organization, Membership], Depends(get_current_org)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[MarkAllReadResponse]:
    org, membership = org_and_membership
    repo = NotificationRepository(session, org.id, redis)
    updated = await repo.mark_all_read(membership.user_id)
    await session.commit()
    return Envelope(
        data=MarkAllReadResponse(updated=updated), meta=Meta(request_id=_request_id(request))
    )


@router.get("/notifications/unread-count")
@api_limit
async def get_unread_count(
    request: Request,
    org_and_membership: Annotated[tuple[Organization, Membership], Depends(get_current_org)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[UnreadCountResponse]:
    org, membership = org_and_membership
    repo = NotificationRepository(session, org.id, redis)
    count = await repo.unread_count(membership.user_id)
    return Envelope(
        data=UnreadCountResponse(count=count), meta=Meta(request_id=_request_id(request))
    )


@router.patch(
    "/me/notification-preferences",
    dependencies=[Depends(reject_during_impersonation("data_mutation"))],
)
@api_limit
async def update_notification_preferences(
    request: Request,
    body: NotificationPreferencesUpdate,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[NotificationPreferencesUpdate]:
    updated = await AuthService(session).update_notification_preferences(user.id, body.preferences)
    await session.commit()
    await invalidate_user_cache(redis, user.id)
    return Envelope(
        data=NotificationPreferencesUpdate(preferences=updated.notification_preferences),
        meta=Meta(request_id=_request_id(request)),
    )


@router.post(
    "/notifications/push-tokens", dependencies=[Depends(reject_during_impersonation("notifying"))]
)
@api_limit
async def register_push_token(
    request: Request,
    body: PushTokenRegisterRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[PushTokenPublic]:
    # User-scoped, not org-scoped (PushTokenRepository takes no org_id, same
    # as auth's SessionRepository) -- a device's push token isn't tied to
    # whichever org happens to be active when the app registers it.
    push_token = await PushTokenRepository(session).register(
        user.id, body.token, body.platform, body.device_label
    )
    await session.commit()
    return Envelope(
        data=PushTokenPublic.model_validate(push_token),
        meta=Meta(request_id=_request_id(request)),
    )
