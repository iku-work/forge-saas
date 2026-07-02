"""
Lazily-created ARQ pool shared by every service that enqueues a background
job (AuthService's verification/reset/magic-link emails, TenancyService's
invitation email, GDPRService's export/purge jobs). Mirrors the
get_settings()/app.core.db.engine singleton pattern already used in this
package, rather than threading a pool through every router + service
constructor for the handful of call sites that need one.

Tests replace `enqueue_job` itself via monkeypatch (see tests/conftest.py's
`enqueued_jobs` fixture) instead of connecting to a real Redis.
"""

from typing import Any

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import get_settings

_pool: ArqRedis | None = None


async def _get_pool() -> ArqRedis:
    global _pool  # noqa: PLW0603 -- guards the module-level singleton below
    if _pool is None:
        settings = get_settings()
        _pool = await create_pool(RedisSettings.from_dsn(str(settings.REDIS_URL)))
    return _pool


async def enqueue_job(function: str, **kwargs: Any) -> None:  # noqa: ANN401 -- forwarded to arq's own **kwargs job
    pool = await _get_pool()
    await pool.enqueue_job(function, **kwargs)


async def dispose_pool() -> None:
    global _pool  # noqa: PLW0603 -- guards the module-level singleton above
    if _pool is not None:
        await _pool.aclose()
        _pool = None
