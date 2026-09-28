"""
Verifies T-034's main.py:
  - an in-process ARQ round trip (enqueue a no-op job against a real Redis,
    run a burst worker, confirm the job actually executed) -- the task's own
    acceptance criterion for "ARQ worker setup",
  - WorkerSettings carries the agreed shape (functions, redis_settings,
    max_jobs, job_timeout, on_startup/on_shutdown),
  - worker_startup/worker_shutdown actually open and tear down the Redis
    client + APScheduler the scheduled maintenance jobs (T-034's scheduler.py)
    run on.
"""

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from arq import create_pool
from arq.connections import RedisSettings
from arq.worker import Worker
from redis.asyncio import Redis
from testcontainers.redis import RedisContainer

from app.core.config import get_settings
from app.workers.jobs.billing import send_dunning_email
from app.workers.jobs.email import send_email
from app.workers.jobs.encryption import rotate_encryption_keys_job
from app.workers.jobs.gdpr import export_user_data, purge_user_data
from app.workers.main import WorkerSettings, worker_shutdown, worker_startup


@pytest.fixture(scope="module")
def redis_container() -> "RedisContainer":
    with RedisContainer("redis:7-alpine") as container:
        yield container


# --- in-process ARQ round trip --------------------------------------------------


# Must be a module-level function: arq registers a job under its
# __qualname__ by default, and pool.enqueue_job() below looks it up by the
# plain string "noop_job" -- a nested function's qualname would carry a
# "<locals>." prefix and the worker would report "function not found".
async def noop_job(ctx: dict[str, Any]) -> str:  # noqa: ARG001 -- ARQ calling convention
    return "ok"


async def test_enqueue_noop_job_executes_via_burst_worker(
    redis_container: "RedisContainer",
) -> None:
    host = redis_container.get_container_host_ip()
    port = int(redis_container.get_exposed_port(6379))
    redis_settings = RedisSettings(host=host, port=port)

    pool = await create_pool(redis_settings)
    try:
        job = await pool.enqueue_job("noop_job")
        assert job is not None

        worker = Worker(
            functions=[noop_job], redis_settings=redis_settings, burst=True, poll_delay=0
        )
        try:
            await worker.async_run()
        finally:
            await worker.close()

        result = await job.result(timeout=5)
    finally:
        await pool.aclose()

    assert result == "ok"


# --- WorkerSettings shape --------------------------------------------------------


def test_worker_settings_has_agreed_shape() -> None:
    assert rotate_encryption_keys_job in WorkerSettings.functions
    assert send_email in WorkerSettings.functions
    assert purge_user_data in WorkerSettings.functions
    assert export_user_data in WorkerSettings.functions
    assert send_dunning_email in WorkerSettings.functions
    assert isinstance(WorkerSettings.redis_settings, RedisSettings)
    assert WorkerSettings.max_jobs == 20
    assert WorkerSettings.job_timeout == 300
    assert WorkerSettings.on_startup is worker_startup
    assert WorkerSettings.on_shutdown is worker_shutdown


# --- worker_startup / worker_shutdown --------------------------------------------


@pytest.fixture
async def _redis_url_env(
    redis_container: "RedisContainer", monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[None, None]:
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    monkeypatch.setenv("REDIS_URL", f"redis://{host}:{port}/0")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.usefixtures("_redis_url_env")
async def test_worker_startup_wires_redis_and_starts_scheduler() -> None:
    ctx: dict[str, Any] = {}

    await worker_startup(ctx)
    try:
        assert isinstance(ctx["redis"], Redis)
        await ctx["redis"].ping()  # proves it's a live, usable client
        assert ctx["scheduler"].running is True
        assert {job.id for job in ctx["scheduler"].get_jobs()} == {
            "cleanup_expired_sessions",
            "cleanup_expired_tokens",
            "sync_expiring_trials",
            "expire_data_export_urls",
            "notify_expiring_trials",
            "expire_trials_to_inactive",
            "report_usage_to_stripe",
            "prune_old_notifications",
            # T-321: retention for webhook_delivery, and the safety net that
            # re-enqueues a due delivery whose deferred ARQ job was lost.
            "prune_webhook_deliveries",
            "sweep_due_webhook_deliveries",
            "reconcile_subscriptions",
            "sweep_orphaned_objects",
            "expire_abandoned_uploads",
            "refresh_active_sessions_gauge",
        }
    finally:
        await worker_shutdown(ctx)

    assert ctx["scheduler"].running is False


@pytest.mark.usefixtures("_redis_url_env")
async def test_worker_shutdown_closes_the_redis_client(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx: dict[str, Any] = {}
    await worker_startup(ctx)
    # redis.asyncio reconnects transparently on the next command, so asserting
    # a post-close command fails is unreliable -- spy on aclose() instead to
    # prove worker_shutdown actually calls it.
    aclose_spy = AsyncMock(wraps=ctx["redis"].aclose)
    monkeypatch.setattr(ctx["redis"], "aclose", aclose_spy)

    await worker_shutdown(ctx)

    aclose_spy.assert_awaited_once()
