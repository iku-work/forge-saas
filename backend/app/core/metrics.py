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
from app.core.db import write_engine

if TYPE_CHECKING:
    from sqlalchemy.pool import QueuePool

ACTIVE_SESSIONS_REDIS_KEY = "metrics:active_sessions"
# T-245: same split as ACTIVE_SESSIONS_REDIS_KEY, for the same reason -- the
# key-rotation job runs in a worker (or a one-off ops process), and neither
# serves /metrics. The job publishes its countdown here; the gauge below
# reads it back at scrape time.
REENCRYPT_REMAINING_REDIS_KEY = "metrics:reencrypt_remaining"
# T-321: same split again. Webhook deliveries are dispatched by the worker,
# and "how many are still owed to somebody" is a query only that process
# makes — but it is `/metrics`, served here, that has to answer for it.
# Deliberately *not* forge_arq_queue_depth: a delivery whose job has already
# been consumed and whose next attempt is two hours away is still outstanding
# work, and the ARQ queue cannot see it.
WEBHOOK_QUEUE_DEPTH_REDIS_KEY = "metrics:webhook_queue_depth"

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
FORGE_BREAKER_STATE = Gauge(
    "forge_breaker_state",
    "Circuit breaker state per integration (0=closed, 1=half-open, 2=open)",
    ["integration"],
)
FORGE_BREAKER_OPEN_TOTAL = Counter(
    "forge_breaker_open_total",
    "Times an integration's circuit breaker has tripped open",
    ["integration"],
)
FORGE_EXTERNAL_CALL_SECONDS = Histogram(
    "forge_external_call_seconds",
    "Time spent in a resilience.resilient()-wrapped outbound call, including its own retries",
    ["integration"],
)
# T-245's key-rotation job (app.workers.jobs.encryption). Label cardinality is
# bounded by the schema itself -- one series per EncryptedString column, of
# which there are two today -- so table/column labels are safe here in a way
# they would not be for, say, a per-row identifier.
FORGE_REENCRYPT_ROWS_TOTAL = Counter(
    "forge_reencrypt_rows_total",
    "Rows rewritten onto the current primary ENCRYPTION_KEY",
    ["table", "column"],
)
FORGE_REENCRYPT_REMAINING = Gauge(
    "forge_reencrypt_remaining",
    "Rows the running key-rotation job has not scanned yet (0 when no rotation is in flight)",
)
FORGE_WEBHOOK_QUEUE_DEPTH = Gauge(
    "forge_webhook_queue_depth",
    "Webhook deliveries awaiting an attempt (published by the worker, not per-scrape)",
)
# Label cardinality is bounded by WebhookDelivery.status's four values, which
# is the bar the FORGE_REENCRYPT_ROWS_TOTAL comment below sets: a label whose
# value set is fixed by the schema is safe, a per-row identifier is not.
FORGE_WEBHOOK_DELIVERIES_TOTAL = Counter(
    "forge_webhook_deliveries_total",
    "Webhook delivery attempts by outcome",
    ["outcome"],
)
FORGE_REENCRYPT_BATCH_SECONDS = Histogram(
    "forge_reencrypt_batch_seconds",
    "Time to scan, re-encrypt and commit one batch of a key rotation",
    ["table", "column"],
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


def _read_published_gauge(key: str) -> float:
    """Reads back a value another process published to Redis. Fails closed to 0.0."""
    try:
        value = cast("bytes | None", _get_sync_redis().get(key))
    except redis.RedisError:
        return 0.0
    return float(value) if value is not None else 0.0


def _read_active_sessions() -> float:
    return _read_published_gauge(ACTIVE_SESSIONS_REDIS_KEY)


def _read_reencrypt_remaining() -> float:
    return _read_published_gauge(REENCRYPT_REMAINING_REDIS_KEY)


def _read_webhook_queue_depth() -> float:
    return _read_published_gauge(WEBHOOK_QUEUE_DEPTH_REDIS_KEY)


def _read_db_pool_used() -> int:
    return cast("QueuePool", write_engine.pool).checkedout()


def _read_db_pool_size() -> int:
    return cast("QueuePool", write_engine.pool).size()


FORGE_DB_POOL_USED.set_function(_read_db_pool_used)
FORGE_DB_POOL_SIZE.set_function(_read_db_pool_size)
FORGE_ARQ_QUEUE_DEPTH.set_function(_read_arq_queue_depth)
FORGE_ACTIVE_SESSIONS.set_function(_read_active_sessions)
FORGE_REENCRYPT_REMAINING.set_function(_read_reencrypt_remaining)
FORGE_WEBHOOK_QUEUE_DEPTH.set_function(_read_webhook_queue_depth)


def init_metrics(app: FastAPI) -> None:
    # should_instrument_requests_inprogress: powers the "in-flight requests"
    # panel on T-204's api-overview.json dashboard -- off by default in this
    # instrumentator version, so it must be requested explicitly.
    Instrumentator(should_instrument_requests_inprogress=True).instrument(app).expose(
        app, endpoint="/metrics", include_in_schema=False
    )
