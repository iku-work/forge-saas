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
from datetime import timedelta

import pytest
from testcontainers.redis import RedisContainer

from app.core import jobs
from app.core.config import get_settings
from app.core.jobs import enqueue_job as real_enqueue_job
from app.core.jobs import revoke_job as real_revoke_job


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

    job_id = await real_enqueue_job(
        "send_email", to="x@example.com", template="welcome", context={}
    )

    assert jobs._pool is not None
    assert job_id is not None
    queued = await jobs._pool.queued_jobs()
    assert any(j.function == "send_email" for j in queued)


@pytest.mark.usefixtures("_point_at_test_redis")
async def test_enqueue_job_returns_the_arq_job_id() -> None:
    job_id = await real_enqueue_job(
        "send_email", to="x@example.com", template="welcome", context={}
    )

    queued = await jobs._pool.queued_jobs()
    assert job_id is not None
    assert any(j.job_id == job_id for j in queued)


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


@pytest.mark.usefixtures("_point_at_test_redis")
async def test_revoke_job_removes_a_still_deferred_job_from_the_queue() -> None:
    job_id = await real_enqueue_job(
        "send_email",
        to="x@example.com",
        template="welcome",
        context={},
        _defer_by=timedelta(days=1),
    )
    assert job_id is not None
    queued = await jobs._pool.queued_jobs()
    assert any(j.job_id == job_id for j in queued)

    await real_revoke_job(job_id)

    queued_after = await jobs._pool.queued_jobs()
    assert not any(j.job_id == job_id for j in queued_after)


@pytest.mark.usefixtures("_point_at_test_redis")
async def test_revoke_job_on_an_unknown_job_id_is_a_no_op() -> None:
    await real_revoke_job("does-not-exist")
