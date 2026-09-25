"""
APScheduler-driven maintenance jobs (T-034).

These are cron-style jobs, not ARQ jobs: they run in-process inside every
worker replica via AsyncIOScheduler, started from app.workers.main.worker_startup.
Without coordination, N replicas would each fire the same job on the same
schedule. with_distributed_lock uses a Redis ``SET NX EX`` to guarantee only
one replica's invocation actually runs per tick — the others see the key
already set and skip. The lock's TTL (not an explicit unlock) is what makes
this safe if a replica dies mid-job: the lock simply expires and the next
scheduled tick on any surviving replica can acquire it.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.db import async_session_factory
from app.core.metrics import ACTIVE_SESSIONS_REDIS_KEY
from app.models._mixins import utcnow
from app.modules.auth.repository import (
    EmailVerificationTokenRepository,
    MagicLinkTokenRepository,
    PasswordResetTokenRepository,
    SessionRepository,
)
from app.modules.billing.repository import SubscriptionRepository
from app.modules.gdpr.repository import DataExportJobRepository
from app.modules.notifications.repository import NotificationRepository
from app.workers.jobs.billing import (
    expire_trials_to_inactive,
    notify_expiring_trials,
    reconcile_subscriptions,
)
from app.workers.jobs.storage import sweep_orphaned_objects
from app.workers.jobs.usage import report_usage_to_stripe
from app.workers.jobs.webhooks import prune_webhook_deliveries, sweep_due_webhook_deliveries

logger = logging.getLogger(__name__)

# Generous relative to any single job's expected runtime, so a slow run
# doesn't lose its own lock mid-execution to the next scheduled tick.
_LOCK_TTL_SECONDS = 300


async def with_distributed_lock(
    redis_client: Redis, job_name: str, func: Callable[[], Awaitable[None]]
) -> None:
    lock_key = f"scheduler:lock:{job_name}"
    acquired = await redis_client.set(lock_key, "1", nx=True, ex=_LOCK_TTL_SECONDS)
    if not acquired:
        logger.info("scheduler: %s lock held by another replica, skipping this tick", job_name)
        return
    try:
        await func()
    finally:
        await redis_client.delete(lock_key)


async def cleanup_expired_sessions(ctx: dict[str, Any]) -> None:  # noqa: ARG001 -- ARQ/APScheduler calling convention
    """Both halves of session ageing, in the order they have to happen.

    `revoke_idle` marks sessions that passed the idle cap; `prune_expired`
    deletes rows that passed the absolute one. Revoking first means a session
    that crossed both since the last tick is recorded as timed out on its way
    to being deleted, rather than skipping that state -- which matters only
    for anyone reading the table, but costs nothing.

    Without the revoke half these rows sat in the table until their thirtieth
    day, and every reader that forgot the idle rule reported them as live.
    """
    async with async_session_factory() as session:
        repo = SessionRepository(session)
        idled = await repo.revoke_idle()
        pruned = await repo.prune_expired()
        await session.commit()
    logger.info(
        "cleanup_expired_sessions: revoked %d idle sessions, pruned %d expired",
        idled,
        pruned,
    )


async def cleanup_expired_tokens(ctx: dict[str, Any]) -> None:  # noqa: ARG001
    async with async_session_factory() as session:
        deleted = {
            "email_verification": await EmailVerificationTokenRepository(session).delete_expired(),
            "password_reset": await PasswordResetTokenRepository(session).delete_expired(),
            "magic_link": await MagicLinkTokenRepository(session).delete_expired(),
        }
        await session.commit()
    logger.info(  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure  # noqa: E501
        "cleanup_expired_tokens: deleted %s", deleted
    )


async def sync_expiring_trials(ctx: dict[str, Any]) -> None:  # noqa: ARG001
    horizon = utcnow() + timedelta(days=3)
    async with async_session_factory() as session:
        count = await SubscriptionRepository(session).count_trialing_expiring_within(horizon)
    logger.info("sync_expiring_trials: %d trial subscriptions expire within 3 days", count)


async def expire_data_export_urls(ctx: dict[str, Any]) -> None:  # noqa: ARG001
    async with async_session_factory() as session:
        expired = await DataExportJobRepository(session).expire_ready_past_expiry()
        await session.commit()
    logger.info("expire_data_export_urls: expired %d data export jobs", expired)


async def prune_old_notifications(ctx: dict[str, Any]) -> None:  # noqa: ARG001
    # T-219/ASVS V8.3.8: the retention job T-101's model/repository docstrings
    # already promised -- delivers on "notifications are hard-deleted by a
    # retention job, never soft-deleted" (app/modules/notifications/models.py).
    cutoff = utcnow() - timedelta(days=get_settings().NOTIFICATION_RETENTION_DAYS)
    async with async_session_factory() as session:
        pruned = await NotificationRepository.prune_older_than(session, cutoff)
        await session.commit()
    logger.info("prune_old_notifications: pruned %d notifications older than %s", pruned, cutoff)


async def refresh_active_sessions_gauge(redis_client: Redis) -> None:
    # T-202's forge_active_sessions gauge lives in the backend process (it
    # serves /metrics), but this count is computed here in the worker -- the
    # only thing bridging the two processes is Redis: this job writes the
    # count, and app.core.metrics reads it back synchronously at scrape time.
    async with async_session_factory() as session:
        count = await SessionRepository(session).count_active()
    await redis_client.set(ACTIVE_SESSIONS_REDIS_KEY, str(count))


def create_scheduler(redis_client: Redis) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    _add_locked_job(
        scheduler,
        redis_client,
        cleanup_expired_sessions,
        "cleanup_expired_sessions",
        CronTrigger(hour="*/6", minute=0, timezone="UTC"),
    )
    _add_locked_job(
        scheduler,
        redis_client,
        cleanup_expired_tokens,
        "cleanup_expired_tokens",
        CronTrigger(minute=0, timezone="UTC"),
    )
    _add_locked_job(
        scheduler,
        redis_client,
        sync_expiring_trials,
        "sync_expiring_trials",
        CronTrigger(hour=8, minute=0, timezone="UTC"),
    )
    _add_locked_job(
        scheduler,
        redis_client,
        expire_data_export_urls,
        "expire_data_export_urls",
        CronTrigger(minute=0, timezone="UTC"),
    )
    _add_locked_job(
        scheduler,
        redis_client,
        notify_expiring_trials,
        "notify_expiring_trials",
        CronTrigger(hour=9, minute=0, timezone="UTC"),
    )
    _add_locked_job(
        scheduler,
        redis_client,
        expire_trials_to_inactive,
        "expire_trials_to_inactive",
        CronTrigger(hour=10, minute=0, timezone="UTC"),
    )
    _add_locked_job(
        scheduler,
        redis_client,
        report_usage_to_stripe,
        "report_usage_to_stripe",
        CronTrigger(minute="*/15", timezone="UTC"),
    )
    # Hourly, not daily: this is the safety net for a webhook that never
    # arrived, and the window between paying and being upgraded is the thing
    # it exists to bound. Hourly against Stripe's rate limits is nothing --
    # one list call per org with a Stripe customer -- and a customer who
    # pays and waits a day for their plan has effectively not been upgraded.
    _add_locked_job(
        scheduler,
        redis_client,
        reconcile_subscriptions,
        "reconcile_subscriptions",
        CronTrigger(minute=20, timezone="UTC"),
    )
    _add_locked_job(
        scheduler,
        redis_client,
        prune_old_notifications,
        "prune_old_notifications",
        CronTrigger(hour=2, minute=0, timezone="UTC"),
    )
    # T-321, alongside the T-036 purge job at the same hour: `webhook_delivery`
    # is the fastest-growing table this template adds, and a delivery log with
    # no retention policy is a disk-full incident with a date on it.
    _add_locked_job(
        scheduler,
        redis_client,
        prune_webhook_deliveries,
        "prune_webhook_deliveries",
        CronTrigger(hour=3, minute=0, timezone="UTC"),
    )
    # Every five minutes rather than hourly: this only re-enqueues deliveries
    # that are already *due*, and the case it exists for -- a Redis restart
    # that lost the deferred jobs -- otherwise leaves every pending delivery
    # stranded until the next tick.
    _add_locked_job(
        scheduler,
        redis_client,
        sweep_due_webhook_deliveries,
        "sweep_due_webhook_deliveries",
        CronTrigger(minute="*/5", timezone="UTC"),
    )
    # The reconciliation half of StorageService.delete_file. The deferred
    # hard-delete job is a single Redis entry seven days out; anything that
    # loses it loses the deletion silently, and the object keeps costing
    # money with no row left pointing at it. Daily is ample -- every row this
    # finds is already past its grace period, so nothing is urgent, and the
    # first run after deploying this will have a backlog to drain in
    # _SWEEP_BATCH_SIZE-sized bites.
    _add_locked_job(
        scheduler,
        redis_client,
        sweep_orphaned_objects,
        "sweep_orphaned_objects",
        CronTrigger(hour=4, minute=0, timezone="UTC"),
    )

    async def _run_refresh_active_sessions_gauge() -> None:
        await with_distributed_lock(
            redis_client,
            "refresh_active_sessions_gauge",
            lambda: refresh_active_sessions_gauge(redis_client),
        )

    scheduler.add_job(
        _run_refresh_active_sessions_gauge,
        trigger=IntervalTrigger(seconds=60, timezone="UTC"),
        id="refresh_active_sessions_gauge",
        replace_existing=True,
    )
    return scheduler


def _add_locked_job(
    scheduler: AsyncIOScheduler,
    redis_client: Redis,
    job: Callable[[dict[str, Any]], Awaitable[None]],
    job_id: str,
    trigger: CronTrigger,
) -> None:
    async def run() -> None:
        # ctx mirrors the one ARQ builds for the jobs in app.workers.main --
        # `redis` is what a job needs to bust a cache after a write (T-240);
        # expire_trials_to_inactive downgrades an org's plan and would
        # otherwise leave the old entitlements cached.
        await with_distributed_lock(redis_client, job_id, lambda: job({"redis": redis_client}))

    scheduler.add_job(run, trigger=trigger, id=job_id, replace_existing=True)
