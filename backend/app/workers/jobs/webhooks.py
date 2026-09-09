"""
ARQ job (T-321): delivers one `WebhookDelivery` to one endpoint, records what
happened, and schedules the next attempt.

## The shape, and why it is this shape

**One job per delivery, not per event.** Two endpoints subscribed to the same
event get two rows and two jobs, so one dead consumer cannot delay the other.

**The retry ladder is explicit** (`WebhookDeliveryService.RETRY_SCHEDULE`:
1 m, 5 m, 30 m, 2 h, 12 h, then dead-lettered) and each attempt is scheduled
with ARQ's `_defer_by` rather than raised as `arq.worker.Retry`. That is a
deliberate difference from `virus_scan`, which does use `Retry`: arq's own
backoff is its business, and the ladder here is a *documented contract with
the consumer* -- it is on the docs page, it is what "a consumer down for an
hour still receives the event" means, and a test drives it with a clock
fixture instead of sleeping. A schedule you cannot state is a schedule you
cannot support.

**Timeouts are 10 s connect and 10 s read.** One slow consumer must never
occupy a worker; that is the difference between a degraded integration and a
degraded product. The whole request is also bounded, so a consumer that
dribbles a response body forever is cut off too.

**SSRF is re-checked here**, immediately before the request, not only when
the endpoint was saved. A hostname that resolved to a public address
yesterday can resolve to `169.254.169.254` today -- DNS belongs to whoever
registered the URL. T-212's `url_guard` was built for this caller and says so.

**The signature covers the exact bytes that are sent.** `payload` is
serialised once, into `body`, and `body` is both signed and posted. Signing a
re-serialised dict is the classic way to ship a webhook nobody can verify.

**Metrics are published through Redis**, not a process-local registry: this
runs in the worker and `/metrics` is served by the API process, which is the
same split `forge_active_sessions` already lives with (T-245's notes explain
why the in-process gauge is wrong here).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx
from loguru import logger

from app.core import jobs
from app.core.config import get_settings
from app.core.db import async_session_factory
from app.core.metrics import (
    FORGE_WEBHOOK_DELIVERIES_TOTAL,
    WEBHOOK_QUEUE_DEPTH_REDIS_KEY,
)
from app.core.module_loader import is_enabled
from app.core.tracing import trace_job
from app.core.url_guard import UnsafeOutboundURLError, validate_outbound_url
from app.models._mixins import utcnow
from app.modules.webhooks.models import (
    MAX_RESPONSE_BODY_CHARS,
    WebhookDelivery,
    WebhookEndpoint,
)
from app.modules.webhooks.repository import WebhookDeliveryRepository
from app.modules.webhooks.service import WebhookDeliveryService, next_attempt_after
from app.modules.webhooks.signing import (
    DELIVERY_HEADER,
    EVENT_HEADER,
    SIGNATURE_HEADER,
    signature_headers,
)

if TYPE_CHECKING:
    from datetime import datetime

    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession

_HTTP_OK_FLOOR = 200
_HTTP_OK_CEILING = 300


def _timeout() -> httpx.Timeout:
    """Read through `get_settings()` at call time, never bound at import.

    This module is imported by `app.workers.main`, which is imported before a
    test's `.env.test` is in play -- the repo's recurring trap (it bit T-223's
    rate limiter and `test_middleware.py`, and T-312's budget is read the same
    way for the same reason).
    """
    seconds = get_settings().WEBHOOK_TIMEOUT_SECONDS
    return httpx.Timeout(seconds, connect=seconds)


@trace_job
async def deliver_webhook(ctx: dict[str, Any], *, delivery_id: str) -> None:
    redis: Redis | None = ctx.get("redis")
    if not is_enabled("outbound_webhooks"):
        # The rows outlive the toggle (that is what MODULE_TABLES is for), so
        # a delivery enqueued before the module was turned off must not still
        # reach a third party after it. Same check, same reasoning, as the
        # API-key auth path's.
        logger.info("webhooks.module_disabled: delivery_id={}", delivery_id)
        return

    async with async_session_factory() as session:
        delivery = await session.get(WebhookDelivery, UUID(delivery_id))
        if delivery is None or delivery.status != "pending":
            logger.info("webhooks.delivery_not_pending: delivery_id={}", delivery_id)
            return
        endpoint = await session.get(WebhookEndpoint, delivery.endpoint_id)
        # `redis` is what the auto-disable path needs to notify the org's
        # owners (guarded on the notifications module being enabled).
        service = WebhookDeliveryService(session, delivery.org_id, redis)

        if endpoint is None or endpoint.deleted_at is not None or not endpoint.enabled:
            # Not a failure of the *endpoint* -- there is nothing to retry
            # against and nothing to count against a breaker that is already
            # open. The row is closed out so the log says what happened.
            await _abandon(session, delivery, "endpoint is disabled or deleted")
            _count("dead")
            return

        try:
            url = validate_outbound_url(endpoint.url)
        except UnsafeOutboundURLError as exc:
            # Dead immediately rather than retried: a URL that resolves
            # somewhere private will still resolve there in twelve hours, and
            # retrying is five more requests at an internal address.
            await _abandon(session, delivery, f"unsafe destination: {exc}")
            _count("dead")
            logger.warning(
                "webhooks.unsafe_destination: endpoint_id={} reason={}", endpoint.id, exc
            )
            return

        body = json.dumps(delivery.payload, separators=(",", ":"), sort_keys=True).encode()
        headers = {
            "Content-Type": "application/json",
            EVENT_HEADER: delivery.event_type,
            DELIVERY_HEADER: str(delivery.delivery_id),
            SIGNATURE_HEADER: signature_headers(service.active_secrets(endpoint), body),
            "User-Agent": "ForgeSaaS-Webhooks/1.0",
        }

        try:
            # follow_redirects=False, spelled out rather than left to httpx's
            # default: a redirect is a *second* destination, and the guard
            # above only judged the first. Following one would be an SSRF hole
            # with an extra hop in it.
            async with httpx.AsyncClient(timeout=_timeout(), follow_redirects=False) as client:
                response = await client.post(url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            updated = await service.record_failure(
                delivery, status_code=None, body=None, error=f"{type(exc).__name__}: {exc}"
            )
            await session.commit()
            await _schedule_next(updated)
            _count("failed" if updated.status == "pending" else "dead")
            await _publish_queue_depth(session, redis)
            return

        text = (response.text or "")[:MAX_RESPONSE_BODY_CHARS]
        if _HTTP_OK_FLOOR <= response.status_code < _HTTP_OK_CEILING:
            await service.record_success(delivery, response.status_code, text)
            await session.commit()
            _count("delivered")
            await _publish_queue_depth(session, redis)
            return

        updated = await service.record_failure(
            delivery,
            status_code=response.status_code,
            body=text,
            error=f"HTTP {response.status_code}",
        )
        await session.commit()
        await _schedule_next(updated)
        _count("failed" if updated.status == "pending" else "dead")
        await _publish_queue_depth(session, redis)


async def _abandon(session: AsyncSession, delivery: WebhookDelivery, reason: str) -> None:
    """Closes a delivery out with no next attempt, and commits.

    Owns its own commit, unlike the service's record_* methods -- there is no
    transaction to piggyback on here and the two callers have nothing else
    pending.
    """
    delivery.status = "dead"
    delivery.error = reason[:500]
    delivery.next_attempt_at = None
    await session.flush()
    await session.commit()


async def _schedule_next(delivery: WebhookDelivery) -> None:
    """Re-queues this same delivery for its next rung of the ladder.

    `_defer_by` is seconds from now, and it is computed from the row's own
    `next_attempt_at` rather than from the schedule again -- so the queue and
    the log can never disagree about when the next attempt is, which is the
    first thing anybody checks when a consumer says "you never retried".
    """
    if delivery.status != "pending" or delivery.next_attempt_at is None:
        return
    delay = max(0.0, (delivery.next_attempt_at - utcnow()).total_seconds())
    await jobs.enqueue_job("deliver_webhook", delivery_id=str(delivery.id), _defer_by=delay)


def _count(outcome: str) -> None:
    FORGE_WEBHOOK_DELIVERIES_TOTAL.labels(outcome=outcome).inc()


async def _publish_queue_depth(session: AsyncSession, redis: Redis | None) -> None:
    """Publishes the pending-delivery count for the API process's gauge.

    Best-effort on purpose: a metric that can fail a delivery is worse than a
    metric that is briefly stale.
    """
    if redis is None:
        return
    try:
        depth = await WebhookDeliveryRepository.count_pending(session)
        await redis.set(WEBHOOK_QUEUE_DEPTH_REDIS_KEY, str(depth))
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        logger.warning("webhooks.queue_depth_publish_failed: {}", exc)


async def sweep_due_webhook_deliveries(ctx: dict[str, Any]) -> None:
    """Safety net for deliveries whose job never ran.

    Two ways that happens, and the second is the common one:

      * ARQ keeps a deferred job in a Redis sorted set, and a Redis that was
        flushed, restored from a snapshot, or restarted without persistence
        loses it.
      * `WebhookEmitter.emit` enqueues inside the caller's transaction, so a
        worker fast enough to pick the job up before that transaction commits
        finds no row and returns. (Enqueueing after the commit would trade
        this for the worse failure: a committed delivery with no job at all
        if the process dies in between.)

    Either way the row stays `pending` with `next_attempt_at` in the past, and
    this re-enqueues it. Everything it finds is already due, so the only cost
    of running when nothing is wrong is one indexed query per tick.
    """
    if not is_enabled("outbound_webhooks"):
        return
    redis: Redis | None = ctx.get("redis")
    async with async_session_factory() as session:
        due = await WebhookDeliveryRepository.list_due(session, utcnow())
        for delivery in due:
            await jobs.enqueue_job("deliver_webhook", delivery_id=str(delivery.id))
        await _publish_queue_depth(session, redis)
    if due:
        logger.info("webhooks.swept_due_deliveries: count={}", len(due))


async def prune_webhook_deliveries(ctx: dict[str, Any]) -> None:  # noqa: ARG001 -- scheduler calling convention
    """Retention (T-321): deletes deliveries older than the configured window.

    `webhook_delivery` is the fastest-growing table this template adds -- one
    row per event per endpoint, each carrying a stored payload -- so it ships
    with a retention policy rather than acquiring one after the first
    disk-full incident. Batched (see `prune_older_than`) so a long backlog
    drains over several ticks instead of taking one long lock on the biggest
    table in the product.
    """
    if not is_enabled("outbound_webhooks"):
        return
    cutoff = _retention_cutoff()
    total = 0
    async with async_session_factory() as session:
        while True:
            deleted = await WebhookDeliveryRepository.prune_older_than(session, cutoff)
            await session.commit()
            total += deleted
            if deleted == 0:
                break
    logger.info("webhooks.pruned_deliveries: count={} cutoff={}", total, cutoff)


def _retention_cutoff() -> datetime:
    from datetime import timedelta  # noqa: PLC0415 -- only needed here

    return utcnow() - timedelta(days=get_settings().WEBHOOK_DELIVERY_RETENTION_DAYS)


__all__ = [
    "deliver_webhook",
    "next_attempt_after",
    "prune_webhook_deliveries",
    "sweep_due_webhook_deliveries",
]
