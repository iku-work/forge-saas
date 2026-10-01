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
async def test_enqueue_job_queues_the_job_under_the_id_it_returns() -> None:
    job_id = await real_enqueue_job(
        "send_email", to="x@example.com", template="welcome", context={}
    )

    queued = await (await jobs._get_pool()).queued_jobs()
    assert job_id is not None
    assert any(j.job_id == job_id and j.function == "send_email" for j in queued)


@pytest.mark.usefixtures("_point_at_test_redis")
async def test_enqueue_still_works_after_the_pool_was_disposed() -> None:
    # Shutdown disposes the pool; anything enqueued after that (a test, a
    # lifespan restart) must get a fresh one rather than a closed connection.
    await real_enqueue_job("send_email", to="x@example.com", template="welcome", context={})
    await jobs.dispose_pool()
    await jobs.dispose_pool()  # twice is harmless

    job_id = await real_enqueue_job(
        "send_email", to="x@example.com", template="welcome", context={}
    )

    queued = await (await jobs._get_pool()).queued_jobs()
    assert any(j.job_id == job_id for j in queued)


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


class TestRedisSettingsTls:
    """`RedisSettings.from_dsn` drops every query parameter except `db`.

    That meant a `rediss://host/0?ssl_ca_certs=/path/ca.crt` DSN -- which
    redis-py honours, and which is how a self-hosted deployment points at a
    private CA -- left arq verifying against the default trust store. The app
    connected over TLS and the worker did not, from one identical setting.
    Reproduced against a real TLS Redis before this was fixed.
    """

    def test_ssl_ca_certs_survives_the_dsn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            get_settings(),
            "REDIS_URL",
            "rediss://localhost:6379/0?ssl_ca_certs=/etc/ssl/ca.crt",
        )

        settings = jobs.redis_settings()

        assert settings.ssl is True
        assert settings.ssl_ca_certs == "/etc/ssl/ca.crt"
        # from_dsn's own parsing must still apply.
        assert settings.database == 0
        assert settings.port == 6379

    def test_ssl_check_hostname_is_parsed_as_a_boolean(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """redis-py takes it as a URL string; arq's field is a bool."""
        monkeypatch.setattr(
            get_settings(),
            "REDIS_URL",
            "rediss://localhost:6379/0?ssl_check_hostname=true",
        )

        assert jobs.redis_settings().ssl_check_hostname is True

    def test_plain_dsn_is_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The private-network case (Render's Key Value) pays nothing."""
        monkeypatch.setattr(get_settings(), "REDIS_URL", "redis://red-abc123:6379/2")

        settings = jobs.redis_settings()

        assert settings.ssl is False
        assert settings.ssl_ca_certs is None
        assert settings.database == 2
