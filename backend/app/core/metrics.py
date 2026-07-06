"""
Prometheus metrics (T-202): RED metrics (rate, errors, duration) for every
endpoint via prometheus-fastapi-instrumentator, plus app-specific gauges,
a histogram, and counters for things the instrumentator can't see on its own.

`/metrics` is never reachable through Caddy in either environment (see
Caddyfile / Caddyfile.prod's `respond /metrics 404` rules, verified by
test_caddy_config.py) -- it's scrapeable only from inside forge-net, where a
Prometheus instance itself would live.

`forge_active_sessions` can't just query the DB on every scrape the way
`forge_db_pool_used`/`forge_db_pool_size` do: the count is refreshed every 60s
by an APScheduler job running in the *worker* process (app.workers.scheduler),
not the backend process that serves /metrics. The worker writes the count to
Redis (ACTIVE_SESSIONS_REDIS_KEY); this module's gauge just reads that cached
value back at scrape time, so a Prometheus scrape never triggers a COUNT query
itself.
"""

from typing import TYPE_CHECKING, cast

import redis
from fastapi import FastAPI
from prometheus_client import Counter, Gauge, Histogram
from prometheus_fastapi_instrumentator import Instrumentator

from app.core.config import get_settings
from app.core.db import engine

if TYPE_CHECKING:
    from sqlalchemy.pool import QueuePool

ACTIVE_SESSIONS_REDIS_KEY = "metrics:active_sessions"

# arq.constants.default_queue_name -- the ZSET arq.connections.ArqRedis.enqueue_job
# writes pending jobs into (score = when the job should run). Not a Redis
# list, despite "LLEN" being the shorthand people reach for -- ZCARD is the
# correct depth query against this key.
_ARQ_QUEUE_KEY = "arq:queue"

FORGE_DB_POOL_USED = Gauge(
    "forge_db_pool_used", "Checked-out connections in the backend's async engine pool"
)
FORGE_DB_POOL_SIZE = Gauge(
    "forge_db_pool_size", "Configured size of the backend's async engine pool"
)
FORGE_ARQ_QUEUE_DEPTH = Gauge("forge_arq_queue_depth", "Pending jobs waiting in the ARQ queue")
FORGE_ACTIVE_SESSIONS = Gauge(
    "forge_active_sessions",
    "Non-revoked, non-expired sessions (refreshed every 60s by a worker job, not per-scrape)",
)
FORGE_STRIPE_WEBHOOK_SECONDS = Histogram(
    "forge_stripe_webhook_seconds", "Time spent handling a single Stripe webhook event"
)
FORGE_EMAILS_SENT_TOTAL = Counter("forge_emails_sent_total", "Emails successfully sent")
FORGE_PUSH_SENT_TOTAL = Counter(
    "forge_push_sent_total", "Push notifications successfully handed off to the Expo push API"
)

_sync_redis: redis.Redis | None = None


def _get_sync_redis() -> redis.Redis:
    # A separate, lazily-created sync client -- collecting a Gauge's value
    # happens synchronously inside prometheus_client's collect(), which can't
    # await the app's existing redis.asyncio connection.
    global _sync_redis  # noqa: PLW0603 -- guards the module-level singleton below
    if _sync_redis is None:
        settings = get_settings()
        _sync_redis = redis.Redis.from_url(str(settings.REDIS_URL))
    return _sync_redis


def _read_arq_queue_depth() -> float:
    try:
        depth = cast("int", _get_sync_redis().zcard(_ARQ_QUEUE_KEY))
    except redis.RedisError:
        return 0.0
    return float(depth)


def _read_active_sessions() -> float:
    try:
        value = cast("bytes | None", _get_sync_redis().get(ACTIVE_SESSIONS_REDIS_KEY))
    except redis.RedisError:
        return 0.0
    return float(value) if value is not None else 0.0


def _read_db_pool_used() -> int:
    return cast("QueuePool", engine.pool).checkedout()


def _read_db_pool_size() -> int:
    return cast("QueuePool", engine.pool).size()


FORGE_DB_POOL_USED.set_function(_read_db_pool_used)
FORGE_DB_POOL_SIZE.set_function(_read_db_pool_size)
FORGE_ARQ_QUEUE_DEPTH.set_function(_read_arq_queue_depth)
FORGE_ACTIVE_SESSIONS.set_function(_read_active_sessions)


def init_metrics(app: FastAPI) -> None:
    Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
