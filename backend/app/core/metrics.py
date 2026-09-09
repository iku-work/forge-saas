"""
Prometheus metrics (T-202): RED metrics (rate, errors, duration) for every
endpoint via prometheus-fastapi-instrumentator, plus app-specific gauges,
a histogram, and counters for things the instrumentator can't see on its own.

`/metrics` is never reachable through Caddy in either environment (see
Caddyfile / Caddyfile.prod's `respond /metrics 404` rules, verified by
test_caddy_config.py) -- it's scrapeable only from inside forge-net, where a
Prometheus instance itself would live.

That is a property of the *edge*, not of this endpoint, and it stops being
true the moment the app is deployed without one -- on Render the app is the
edge, and this route answered 200 to the open internet. So the endpoint now
carries `require_metrics_token` below, and Settings refuses to boot outside
dev unless the deployment has answered for one or the other (METRICS_TOKEN /
METRICS_BLOCKED_AT_EDGE). Nothing changes for the compose stack, which sets
the latter.

`forge_active_sessions` can't just query the DB on every scrape the way
`forge_db_pool_used`/`forge_db_pool_size` do: the count is refreshed every 60s
by an APScheduler job running in the *worker* process (app.workers.scheduler),
not the backend process that serves /metrics. The worker writes the count to
Redis (ACTIVE_SESSIONS_REDIS_KEY); this module's gauge just reads that cached
value back at scrape time, so a Prometheus scrape never triggers a COUNT query
itself.
"""

import logging
import secrets
import time
from typing import TYPE_CHECKING, cast

import redis
from fastapi import Depends, FastAPI, HTTPException, Request
from prometheus_client import Counter, Gauge, Histogram, start_http_server
from prometheus_fastapi_instrumentator import Instrumentator
from redis.backoff import NoBackoff
from redis.retry import Retry

from app.core.config import get_settings
from app.core.db import write_engine

logger = logging.getLogger(__name__)

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

# A scrape has to answer quickly or not at all. Prometheus's default scrape
# timeout is 10s and *four* gauges below each make their own Redis call, so
# this is a per-call share of that budget, not the budget itself -- at 2.0s
# with one retry apiece a Redis outage made /metrics take 16s and time out,
# which is the opposite of what a monitoring endpoint is for.
_SCRAPE_TIMEOUT_SECONDS = 1.0

_sync_redis: redis.Redis | None = None


def _get_sync_redis() -> redis.Redis:
    # A separate, lazily-created sync client -- collecting a Gauge's value
    # happens synchronously inside prometheus_client's collect(), which can't
    # await the app's existing redis.asyncio connection.
    global _sync_redis  # noqa: PLW0603 -- guards the module-level singleton below
    if _sync_redis is None:
        settings = get_settings()
        # Explicit timeouts, because the readers below "fail closed to 0.0" on
        # a RedisError and without these there is no RedisError to catch: a
        # connect to an unreachable Redis blocks until the OS gives up, and
        # the caller is prometheus_client's collect(), on the scrape thread.
        # The symptom is a /metrics endpoint that hangs rather than reports
        # zeros -- so the first thing an operator reaches for during a Redis
        # outage is the thing that stops answering.
        _sync_redis = redis.Redis.from_url(
            str(settings.REDIS_URL),
            socket_connect_timeout=_SCRAPE_TIMEOUT_SECONDS,
            socket_timeout=_SCRAPE_TIMEOUT_SECONDS,
            # No retry: a scrape that cannot reach Redis wants the zero now.
            # Retrying doubles every gauge's cost at exactly the moment the
            # endpoint is being read to find out what is wrong.
            retry=Retry(NoBackoff(), 0),
            retry_on_timeout=False,
        )
    return _sync_redis


# Four gauges below read Redis, independently, on every scrape. When Redis is
# down each one pays the connect timeout, so the honest per-call bound above
# still adds up to four times itself -- 8s against Prometheus's 10s default,
# which is a scrape that fails rather than a scrape that reports zeros.
#
# So the *first* failure of a scrape stands the rest of them down for a
# while. This is deliberately not app/core/resilience.py's pybreaker: these
# run inside prometheus_client's synchronous collect(), the failure mode is
# "answer 0.0 immediately" rather than "raise", and there is nothing here to
# half-open -- the next scrape after the window simply tries again.
_REDIS_DOWN_WINDOW_SECONDS = 30.0
_redis_unreachable_until = 0.0


def _redis_or_none() -> redis.Redis | None:
    """The scrape client, or None while Redis is known to be unreachable."""
    if time.monotonic() < _redis_unreachable_until:
        return None
    return _get_sync_redis()


def _note_redis_failure() -> None:
    global _redis_unreachable_until  # noqa: PLW0603 -- guards the window above
    _redis_unreachable_until = time.monotonic() + _REDIS_DOWN_WINDOW_SECONDS


def reset_scrape_backoff() -> None:
    """Forget that Redis was unreachable.

    Public because the window above is *process*-global, which is right for a
    server and wrong for a test suite: one test that makes a gauge fail --
    tests/test_metrics.py has one on purpose -- would otherwise stand the
    gauges down for every test that ran in the next thirty seconds, and the
    symptom is a gauge reading 0.0 in a full run and correctly alone. The root
    conftest calls this between tests. Nothing in the app should.
    """
    global _redis_unreachable_until  # noqa: PLW0603 -- guards the window above
    _redis_unreachable_until = 0.0


def _read_arq_queue_depth() -> float:
    client = _redis_or_none()
    if client is None:
        return 0.0
    try:
        depth = cast("int", client.zcard(_ARQ_QUEUE_KEY))
    except redis.RedisError:
        _note_redis_failure()
        return 0.0
    return float(depth)


def _read_published_gauge(key: str) -> float:
    """Reads back a value another process published to Redis. Fails closed to 0.0."""
    client = _redis_or_none()
    if client is None:
        return 0.0
    try:
        value = cast("bytes | None", client.get(key))
    except redis.RedisError:
        _note_redis_failure()
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


def start_worker_metrics_server(port: int) -> None:
    """Expose this process's metrics over HTTP, for the worker (T-350).

    Without this, every counter incremented in the worker is invisible:
    Prometheus scrapes `backend:8000`, the two processes have separate
    registries, and a Counter cannot be published through Redis the way the
    gauges above are -- `set_function` has no counter equivalent, and a
    monotonically increasing value reconstructed from a shared key would
    double-count across worker replicas. So `forge_webhook_deliveries_total`,
    `forge_emails_sent_total` and `forge_push_sent_total` all read as a flat
    zero on a dashboard, which is indistinguishable from "nothing is being
    delivered" -- the exact reading somebody would act on during an incident.

    A second scrape target, rather than a push gateway: the worker is a
    long-lived process on the same network as Prometheus, which is the case
    scraping is for. The gauges keep their Redis path regardless -- queue
    depth has to be readable when the worker is *down*, which is precisely
    when this endpoint is not answering.

    Best-effort by design. A worker that cannot bind its metrics port must
    still process jobs: observability is not a reason to refuse work, and the
    failure is logged where an operator will see it.
    """
    try:
        start_http_server(port)
    except OSError:
        logger.exception("could not start the worker metrics server on port %d", port)
    else:
        logger.info("worker metrics available on :%d/metrics", port)


def require_metrics_token(request: Request) -> None:
    """Gate /metrics on a bearer token when one is configured.

    A no-op when METRICS_TOKEN is unset, which is the dev default and the
    docker-compose posture -- there, `respond /metrics 404` in the Caddyfile
    is what keeps the endpoint unreachable, and Settings refuses to boot
    outside dev unless one of the two is true. `compare_digest` because this
    is a plain secret equality check on an unauthenticated route.
    """
    token = get_settings().METRICS_TOKEN
    if not token:
        return
    header = request.headers.get("authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(presented, token):
        # 404, not 401: an unauthenticated caller learns nothing about
        # whether this deployment scrapes metrics at all.
        raise HTTPException(status_code=404)


def init_metrics(app: FastAPI) -> None:
    # should_instrument_requests_inprogress: powers the "in-flight requests"
    # panel on T-204's api-overview.json dashboard -- off by default in this
    # instrumentator version, so it must be requested explicitly.
    Instrumentator(should_instrument_requests_inprogress=True).instrument(app).expose(
        app,
        endpoint="/metrics",
        include_in_schema=False,
        # expose() forwards **kwargs to app.get(), so the endpoint it
        # registers carries this dependency like any other route.
        dependencies=[Depends(require_metrics_token)],
    )
