"""
ARQ worker entrypoint (T-034): `arq app.workers.main.WorkerSettings`
(see docker-compose.yml's `worker` service). Shares the FastAPI app's async
engine via app.core.db's module-level singleton -- each process (backend,
worker) gets its own engine instance from that import, so no cross-process
connection sharing is involved.
"""

from typing import Any, ClassVar

from arq.connections import RedisSettings
from arq.typing import WorkerCoroutine
from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.db import dispose_engine
from app.workers.jobs.email import send_email
from app.workers.jobs.encryption import reencrypt_column_job
from app.workers.jobs.gdpr import export_user_data, purge_user_data
from app.workers.jobs.notifications import backfill_notification_preferences, send_push
from app.workers.jobs.virus_scan import virus_scan
from app.workers.scheduler import create_scheduler


async def worker_startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
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
        reencrypt_column_job,
        send_email,
        purge_user_data,
        export_user_data,
        backfill_notification_preferences,
        send_push,
        virus_scan,
    ]
    redis_settings = RedisSettings.from_dsn(str(get_settings().REDIS_URL))
    max_jobs = 20
    job_timeout = 300
    on_startup = worker_startup
    on_shutdown = worker_shutdown
