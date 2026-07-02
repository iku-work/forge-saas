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
from redis.asyncio import Redis

from app.core.db import async_session_factory
from app.models._mixins import utcnow
from app.modules.auth.repository import (
    EmailVerificationTokenRepository,
    MagicLinkTokenRepository,
    PasswordResetTokenRepository,
    SessionRepository,
)
from app.modules.billing.repository import SubscriptionRepository
from app.modules.gdpr.repository import DataExportJobRepository
from app.workers.jobs.billing import expire_trials_to_inactive, notify_expiring_trials

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
    async with async_session_factory() as session:
        pruned = await SessionRepository(session).prune_expired()
        await session.commit()
    logger.info("cleanup_expired_sessions: pruned %d expired sessions", pruned)


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
    return scheduler


def _add_locked_job(
    scheduler: AsyncIOScheduler,
    redis_client: Redis,
    job: Callable[[dict[str, Any]], Awaitable[None]],
    job_id: str,
    trigger: CronTrigger,
) -> None:
    async def run() -> None:
        await with_distributed_lock(redis_client, job_id, lambda: job({}))

    scheduler.add_job(run, trigger=trigger, id=job_id, replace_existing=True)
