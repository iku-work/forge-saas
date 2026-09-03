"""
ARQ worker entrypoint (T-034): `arq app.workers.main.WorkerSettings`
(see docker-compose.yml's `worker` service). Shares the FastAPI app's async
engine via app.core.db's module-level singleton -- each process (backend,
worker) gets its own engine instance from that import, so no cross-process
connection sharing is involved.
"""

from typing import Any, ClassVar

from arq.typing import WorkerCoroutine
from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.db import dispose_engine
from app.core.jobs import redis_settings as build_redis_settings
from app.core.metrics import start_worker_metrics_server
from app.workers.jobs.billing import send_dunning_email
from app.workers.jobs.email import send_email
from app.workers.jobs.encryption import rotate_encryption_keys_job
from app.workers.jobs.gdpr import export_user_data, purge_user_data
from app.workers.jobs.notifications import backfill_notification_preferences, send_push
from app.workers.jobs.virus_scan import virus_scan
from app.workers.jobs.webhooks import deliver_webhook
from app.workers.scheduler import create_scheduler


async def worker_startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    # T-350: this process has its own Prometheus registry, so anything it
    # counts is invisible until something scrapes *it*. See
    # metrics.start_worker_metrics_server for why a counter cannot take the
    # Redis path the gauges use.
    start_worker_metrics_server(settings.WORKER_METRICS_PORT)
    ctx["redis"] = Redis.from_url(str(settings.REDIS_URL))
    scheduler = create_scheduler(ctx["redis"])
    scheduler.start()
    ctx["scheduler"] = scheduler


async def worker_shutdown(ctx: dict[str, Any]) -> None:
    scheduler = ctx.get("scheduler")
    if scheduler is not None:
        scheduler.shutdown(wait=False)
    redis_client = ctx.get("redis")
    if redis_client is not None:
        await redis_client.aclose()
    await dispose_engine()


class WorkerSettings:
    functions: ClassVar[list[WorkerCoroutine]] = [
        rotate_encryption_keys_job,
        send_email,
        purge_user_data,
        export_user_data,
        backfill_notification_preferences,
        send_push,
        virus_scan,
        send_dunning_email,
        # T-321. Registered unconditionally, like every other job here: the
        # function itself checks `is_enabled("outbound_webhooks")`, so a
        # delivery enqueued before the module was switched off is dropped
        # rather than sent -- which a missing registration could not do (arq
        # would simply fail to resolve the job name and log it as an error).
        deliver_webhook,
    ]
    # Shared with app.core.jobs so the enqueue side and the consume side
    # cannot disagree about TLS -- see redis_settings()'s docstring.
    redis_settings = build_redis_settings()
    max_jobs = 20
    job_timeout = 300
    on_startup = worker_startup
    on_shutdown = worker_shutdown
