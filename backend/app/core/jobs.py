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

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from arq.constants import default_queue_name, job_key_prefix

from app.core.config import get_settings

_pool: ArqRedis | None = None


async def _get_pool() -> ArqRedis:
    global _pool  # noqa: PLW0603 -- guards the module-level singleton below
    if _pool is None:
        settings = get_settings()
        _pool = await create_pool(RedisSettings.from_dsn(str(settings.REDIS_URL)))
    return _pool


async def enqueue_job(function: str, **kwargs: Any) -> str | None:  # noqa: ANN401 -- forwarded to arq's own **kwargs job
    pool = await _get_pool()
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
