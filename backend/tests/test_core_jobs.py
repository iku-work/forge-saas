"""
Verifies app.core.jobs: the lazily-created ARQ pool shared by every service
that enqueues a background job (AuthService, TenancyService, GDPRService).
Real Redis via testcontainers, matching the rest of the suite's "real
Postgres + Redis, no mocking" policy -- enqueue_job is imported directly so
this file exercises the genuine pool-creation/enqueue/dispose path rather
than tests/conftest.py's autouse `enqueued_jobs` fixture, which monkeypatches
the module attribute for every other test in the suite.
"""

from collections.abc import AsyncGenerator

import pytest
from testcontainers.redis import RedisContainer

from app.core import jobs
from app.core.config import get_settings
from app.core.jobs import enqueue_job as real_enqueue_job


@pytest.fixture(autouse=True)
async def _reset_pool() -> AsyncGenerator[None, None]:
    yield
    await jobs.dispose_pool()


@pytest.fixture
def _point_at_test_redis(redis_container: RedisContainer, monkeypatch: pytest.MonkeyPatch) -> None:
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    monkeypatch.setenv("REDIS_URL", f"redis://{host}:{port}/3")
    get_settings.cache_clear()


@pytest.mark.usefixtures("_point_at_test_redis")
async def test_enqueue_job_creates_pool_lazily_and_enqueues() -> None:
    assert jobs._pool is None

    await real_enqueue_job("send_email", to="x@example.com", template="welcome", context={})

    assert jobs._pool is not None
    queued = await jobs._pool.queued_jobs()
    assert any(j.function == "send_email" for j in queued)


@pytest.mark.usefixtures("_point_at_test_redis")
async def test_get_pool_reuses_existing_pool() -> None:
    first = await jobs._get_pool()
    second = await jobs._get_pool()

    assert first is second


@pytest.mark.usefixtures("_point_at_test_redis")
async def test_dispose_pool_closes_and_clears() -> None:
    await jobs._get_pool()
    assert jobs._pool is not None

    await jobs.dispose_pool()

    assert jobs._pool is None


async def test_dispose_pool_without_a_pool_is_a_no_op() -> None:
    assert jobs._pool is None

    await jobs.dispose_pool()

    assert jobs._pool is None
