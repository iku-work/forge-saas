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
endpoints below do. Instead two plain Redis counters (per-user, and a global
one from settings.SSE_MAX_CONNECTIONS) gate *opening* a connection -- checked
and incremented before the StreamingResponse/generator is even constructed,
so a rejection returns a normal envelope response rather than ever entering
the stream.

Pinging on a per-message timeout (pubsub.get_message(..., timeout=30)
returning None) rather than a separate clock task keeps the implementation
to one coroutine: a real event always preempts the timer, and 30s of silence
always produces a ": ping" keepalive comment, satisfying both halves of the
spec's requirement with nothing extra to clean up on disconnect.
"""

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
)
from app.core.envelope import Envelope, Meta
from app.core.exceptions import RateLimitError
from app.middleware.rate_limit import api_limit
from app.modules.auth.models import User
from app.modules.auth.service import AuthService
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
_TOTAL_CONNECTIONS_KEY = "notif:sse:conns:total"


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid4())


def _user_connections_key(user_id: UUID) -> str:
    return f"notif:sse:conns:{user_id}"


async def _acquire_connection_slot(redis: Redis, user_id: UUID) -> None:
    user_count = await redis.incr(_user_connections_key(user_id))
    if user_count > _PER_USER_CONNECTION_LIMIT:
        await redis.decr(_user_connections_key(user_id))
        raise RateLimitError(
            "errors.too_many_notification_streams",
            fallback="Too many open notification streams for this account",
        )

    total_count = await redis.incr(_TOTAL_CONNECTIONS_KEY)
    if total_count > get_settings().SSE_MAX_CONNECTIONS:
        await redis.decr(_TOTAL_CONNECTIONS_KEY)
        await redis.decr(_user_connections_key(user_id))
        raise RateLimitError(
            "errors.notification_stream_capacity_exceeded",
            fallback="Notification stream capacity exceeded, try again shortly",
        )


async def _release_connection_slot(redis: Redis, user_id: UUID) -> None:
    await redis.decr(_user_connections_key(user_id))
    await redis.decr(_TOTAL_CONNECTIONS_KEY)


async def _event_stream(redis: Redis, user_id: UUID) -> AsyncGenerator[str, None]:
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
        await _release_connection_slot(redis, user_id)


@router.get("/notifications/stream")
async def stream_notifications(
    user: Annotated[User, Depends(get_current_user)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> StreamingResponse:
    await _acquire_connection_slot(redis, user.id)
    return StreamingResponse(
        _event_stream(redis, user.id),
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
    after: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    unread_only: Annotated[bool, Query()] = False,
) -> Envelope[list[NotificationPublic]]:
    org, membership = org_and_membership
    repo = NotificationRepository(session, org.id, redis)
    notifications, next_cursor = await repo.list_for_user(
        membership.user_id, after, limit, unread_only=unread_only
    )
    data = [NotificationPublic.model_validate(n) for n in notifications]
    meta = Meta(
        request_id=_request_id(request),
        next_cursor=str(next_cursor) if next_cursor is not None else None,
    )
    return Envelope(data=data, meta=meta)


@router.patch("/notifications/{notification_id}/read")
@api_limit
async def mark_notification_read(
    request: Request,
    notification_id: UUID,
    org_and_membership: Annotated[tuple[Organization, Membership], Depends(get_current_org)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[NotificationPublic]:
    org, membership = org_and_membership
    repo = NotificationRepository(session, org.id, redis)
    notification = await repo.mark_read(notification_id, membership.user_id)
    await session.commit()
    return Envelope(
        data=NotificationPublic.model_validate(notification),
        meta=Meta(request_id=_request_id(request)),
    )


@router.post("/notifications/read-all")
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


@router.patch("/me/notification-preferences")
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


@router.post("/notifications/push-tokens")
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
