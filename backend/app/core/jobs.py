"""
Lazily-created ARQ pool shared by every service that enqueues a background
job (AuthService's verification/reset/magic-link emails, TenancyService's
invitation email, GDPRService's export/purge jobs, DunningService's dunning
email sequence). Mirrors the get_settings()/app.core.db.engine singleton
pattern already used in this package, rather than threading a pool through
every router + service constructor for the handful of call sites that need
one.

Tests replace `enqueue_job` itself via monkeypatch (see tests/conftest.py's
`enqueued_jobs` fixture) instead of connecting to a real Redis.

revoke_job (T-130) removes a still-deferred job's entry from the queue's
sorted set and deletes its stashed payload directly, rather than going
through arq.jobs.Job.abort() -- that method blocks waiting for a worker to
observe an abort signal and record a result, which has no worker to satisfy
it when called from a request/webhook handler. A job this deletes has either
not run yet (the common case -- DunningService cancels a campaign days
before its next step is due) or has already run and expired out of the
queue on its own, in which case this is a harmless no-op.
"""

from typing import Any
from urllib.parse import parse_qs, urlparse

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from arq.constants import default_queue_name, job_key_prefix

from app.core.config import get_settings

_pool: ArqRedis | None = None

# TLS query parameters redis-py accepts on a DSN, mapped to the RedisSettings
# fields arq exposes for the same thing.
_SSL_DSN_PARAMS = {
    "ssl_ca_certs": "ssl_ca_certs",
    "ssl_certfile": "ssl_certfile",
    "ssl_keyfile": "ssl_keyfile",
    "ssl_cert_reqs": "ssl_cert_reqs",
    "ssl_check_hostname": "ssl_check_hostname",
}


def redis_settings() -> RedisSettings:
    """arq connection settings for REDIS_URL, TLS parameters included.

    `RedisSettings.from_dsn` parses scheme, host, port, credentials and `db`
    and **silently drops every other query parameter** -- so a
    `rediss://host/0?ssl_ca_certs=/path/ca.crt` DSN, which redis-py honours
    (and which is how a self-hosted deployment points at a private CA), left
    arq verifying against the default trust store instead. The app connected
    and the worker did not, from one identical setting.

    Both arq call sites go through here so they cannot drift apart again.
    """
    url = str(get_settings().REDIS_URL)
    settings = RedisSettings.from_dsn(url)
    query = parse_qs(urlparse(url).query)
    for param, field in _SSL_DSN_PARAMS.items():
        values = query.get(param)
        if not values:
            continue
        value: object = values[0]
        if field == "ssl_check_hostname":
            value = values[0].lower() in ("1", "true", "yes")
        setattr(settings, field, value)
    return settings


async def _get_pool() -> ArqRedis:
    global _pool  # noqa: PLW0603 -- guards the module-level singleton below
    if _pool is None:
        _pool = await create_pool(redis_settings())
    return _pool


# Review R4: virus scans run on their own queue, consumed by their own worker
# (app.workers.main.ScanWorkerSettings, a separate container). A burst of
# uploads then fills scan slots, not the slots email, deletion and webhook
# delivery need -- and a scan that exhausts memory takes down only scanning.
SCAN_QUEUE_NAME = "forge:queue:scan"

# Every job not listed here runs on arq's default queue. Routing lives here,
# not at call sites, so no caller can forget it -- a job enqueued onto a
# queue whose worker does not register it is accepted and never runs, which
# tests/test_worker_registration.py checks per (queue, name) pair.
JOB_QUEUES: dict[str, str] = {"virus_scan": SCAN_QUEUE_NAME}


def queue_for(function: str) -> str:
    return JOB_QUEUES.get(function, default_queue_name)


async def enqueue_job(function: str, **kwargs: Any) -> str | None:  # noqa: ANN401 -- forwarded to arq's own **kwargs job
    pool = await _get_pool()
    kwargs.setdefault("_queue_name", queue_for(function))
    job = await pool.enqueue_job(function, **kwargs)
    return job.job_id if job is not None else None


async def revoke_job(job_id: str) -> None:
    pool = await _get_pool()
    async with pool.pipeline(transaction=True) as pipe:
        pipe.zrem(default_queue_name, job_id)
        pipe.delete(job_key_prefix + job_id)
        await pipe.execute()


async def dispose_pool() -> None:
    global _pool  # noqa: PLW0603 -- guards the module-level singleton above
    if _pool is not None:
        await _pool.aclose()
        _pool = None
