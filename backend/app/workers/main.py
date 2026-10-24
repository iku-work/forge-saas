"""
ARQ worker entrypoint (T-034): `arq app.workers.main.WorkerSettings`
(see docker-compose.yml's `worker` service). Shares the FastAPI app's async
engine via app.core.db's module-level singleton -- each process (backend,
worker) gets its own engine instance from that import, so no cross-process
connection sharing is involved.
"""

import logging
from typing import Any, ClassVar

from arq.typing import WorkerCoroutine
from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.db import dispose_engine
from app.core.jobs import SCAN_QUEUE_NAME
from app.core.jobs import redis_settings as build_redis_settings
from app.core.logging import configure_logging
from app.core.metrics import start_worker_metrics_server
from app.core.storage_endpoint import enforce_storage_endpoint_is_reachable_internally
from app.workers.jobs.billing import send_dunning_email
from app.workers.jobs.email import send_email
from app.workers.jobs.encryption import rotate_encryption_keys_job
from app.workers.jobs.gdpr import export_user_data, purge_user_data
from app.workers.jobs.notifications import backfill_notification_preferences, send_push
from app.workers.jobs.storage import hard_delete_stored_file
from app.workers.jobs.virus_scan import virus_scan
from app.workers.jobs.webhooks import deliver_webhook
from app.workers.scheduler import create_scheduler


def _configure_worker_logging() -> None:
    # The arq CLI configures only its own `arq` logger, so without this every
    # app.* logger.info in a job was dropped and WARNING+ went out unscrubbed
    # through logging.lastResort. arq's own handler is removed so its lines
    # reach stdout once, through the same JSON sink, rather than twice.
    configure_logging()
    logging.getLogger("arq").handlers.clear()


async def worker_startup(ctx: dict[str, Any]) -> None:
    _configure_worker_logging()
    settings = get_settings()
    # This process makes S3 calls of its own (object deletion, exports), so
    # it needs the same route the API needs -- and it is the process most
    # likely to be restarted alone, without anyone watching the API's boot.
    enforce_storage_endpoint_is_reachable_internally(settings)
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
        send_dunning_email,
        # T-321. Registered unconditionally, like every other job here: the
        # function itself checks `is_enabled("outbound_webhooks")`, so a
        # delivery enqueued before the module was switched off is dropped
        # rather than sent -- which a missing registration could not do (arq
        # would simply fail to resolve the job name and log it as an error).
        deliver_webhook,
        # StorageService.delete_file has enqueued this name since T-112.
        # Until it appeared here, arq had nothing to resolve it to and every
        # deletion left its object in the bucket -- see the job's module
        # docstring, and tests/test_worker_registration.py for the sweep that
        # now stops a name/registration pair drifting apart again.
        hard_delete_stored_file,
    ]
    # Shared with app.core.jobs so the enqueue side and the consume side
    # cannot disagree about TLS -- see redis_settings()'s docstring.
    redis_settings = build_redis_settings()
    max_jobs = 20
    job_timeout = 300
    # PROD-06. arq's default is 3600s, which makes `arq --check` -- the
    # worker's Docker healthcheck -- useless: a wedged worker would look
    # healthy for the better part of an hour. The heartbeat is written from
    # the main loop, so a job that blocks that loop stops it, which is
    # exactly the failure a liveness probe is for. The key's TTL is this
    # plus one second, so a stuck worker fails its check within ~31s.
    health_check_interval = 30
    on_startup = worker_startup
    on_shutdown = worker_shutdown


async def scan_worker_startup(ctx: dict[str, Any]) -> None:
    _configure_worker_logging()
    settings = get_settings()
    # Streams every object it scans from the object store.
    enforce_storage_endpoint_is_reachable_internally(settings)
    # Its own container, so its own metrics endpoint on the same port.
    start_worker_metrics_server(settings.WORKER_METRICS_PORT)
    ctx["redis"] = Redis.from_url(str(settings.REDIS_URL))
    # Deliberately no scheduler: scheduled jobs belong to WorkerSettings, and
    # two schedulers would fire every cron job twice.


class ScanWorkerSettings:
    """`arq app.workers.main.ScanWorkerSettings` -- the scanner-worker service.

    Review R4. Virus scans used to share WorkerSettings' twenty slots and
    its memory cap with email, deletion and webhook delivery, so a burst of
    maximum-size uploads could hold every slot, or run the process out of
    memory, and stall everything else. They now run here, on their own
    queue (app.core.jobs routes `virus_scan` to it), in their own container
    with its own memory limit.
    """

    functions: ClassVar[list[WorkerCoroutine]] = [virus_scan]
    queue_name = SCAN_QUEUE_NAME
    redis_settings = build_redis_settings()
    max_jobs = get_settings().SCAN_MAX_CONCURRENCY
    job_timeout = 300
    # Same reasoning as WorkerSettings: arq's hour-long default makes
    # `arq --check` meaningless as a liveness probe.
    health_check_interval = 30
    on_startup = scan_worker_startup
    on_shutdown = worker_shutdown
