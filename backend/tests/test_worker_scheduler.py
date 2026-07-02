"""
Verifies T-034's scheduler.py:
  - each maintenance job is callable with a mocked ARQ-style ctx (the task's
    own acceptance criterion) and does the right thing against a real DB,
  - with_distributed_lock actually keeps a second concurrent caller from
    re-running a job while the first instance holds the lock, and releases
    the lock afterwards so the next scheduled tick can run,
  - create_scheduler wires all four jobs onto the expected cron cadence, in
    UTC regardless of host timezone.

Each job under test opens its own session (patched to the test engine, see
_use_test_engine below) -- a *different* session than the one tests use to
seed rows. Because the app's sessions are expire_on_commit=False (db.py), a
seeding session's identity map would keep serving its own stale in-memory
copy of a row after the job's session updates it elsewhere, even though a
fresh `SELECT` would show the new value -- so verification after a job runs
goes through a brand new session (`session_factory()`), never the seeding
session, exactly like a real second request would see it.

Log assertions attach a handler directly to the "app.workers.scheduler"
logger rather than using pytest's `caplog` fixture: test_import_linter.py
calls import-linter's CLI in-process, which runs `logging.config.dictConfig`
without `disable_existing_loggers=False` -- the stdlib default then disables
every logger that already existed (this one included) for the rest of the
test session, so caplog (and anything else relying on root-logger
propagation) silently stops capturing once that test has run. See
scheduler_log_messages below for how this is worked around.
"""

import logging
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from app.modules.auth.models import (
    EmailVerificationToken,
    MagicLinkToken,
    PasswordResetToken,
    User,
)
from app.modules.auth.models import Session as SessionModel
from app.modules.auth.repository import (
    EmailVerificationTokenRepository,
    MagicLinkTokenRepository,
    PasswordResetTokenRepository,
    SessionRepository,
)
from app.modules.billing.models import Plan, Subscription
from app.modules.billing.repository import SubscriptionRepository
from app.modules.gdpr.models import DataExportJob
from app.modules.gdpr.repository import DataExportJobRepository
from app.workers import scheduler as scheduler_module
from app.workers.scheduler import (
    cleanup_expired_sessions,
    cleanup_expired_tokens,
    create_scheduler,
    expire_data_export_urls,
    sync_expiring_trials,
    with_distributed_lock,
)

_TABLES = [
    User.__table__,
    SessionModel.__table__,
    EmailVerificationToken.__table__,
    PasswordResetToken.__table__,
    MagicLinkToken.__table__,
    Plan.__table__,
    Subscription.__table__,
    DataExportJob.__table__,
]


def _past(hours: int = 1) -> datetime:
    return datetime.now(UTC) - timedelta(hours=hours)


def _future(hours: int = 1) -> datetime:
    return datetime.now(UTC) + timedelta(hours=hours)


@pytest.fixture(scope="module")
def postgres_container() -> "PostgresContainer":
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


# Function-scoped (not module-scoped): see test_db.py -- an asyncpg-backed
# engine can't be reused across pytest-asyncio's per-test loop.
@pytest.fixture
async def test_engine(postgres_container: "PostgresContainer") -> AsyncGenerator[AsyncEngine, None]:
    url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all, tables=_TABLES)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all, tables=_TABLES)
    await engine.dispose()


@pytest.fixture(autouse=True)
def _use_test_engine(monkeypatch: pytest.MonkeyPatch, test_engine: AsyncEngine) -> None:
    # The job functions under test open their own session via the module-level
    # async_session_factory (they don't take a session param -- see
    # app.workers.jobs.encryption for the same shape) -- point that factory at
    # the test engine, same pattern as test_encryption_job.py.
    factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(scheduler_module, "async_session_factory", factory)


@pytest.fixture
def session_factory(test_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as s:
        yield s


async def _seed_user(session: AsyncSession) -> User:
    user = User(email=f"{uuid4()}@example.com")
    session.add(user)
    await session.flush()
    return user


async def _seed_plan(session: AsyncSession) -> Plan:
    plan = Plan(
        name="Pro",
        slug=f"pro-{uuid4().hex[:8]}",
        stripe_price_id_monthly="price_m",
        stripe_price_id_yearly="price_y",
        price_monthly=2900,
        price_yearly=29000,
        features={"seats": 5},
    )
    session.add(plan)
    await session.flush()
    return plan


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@pytest.fixture
def scheduler_log_messages() -> Generator[list[str], None, None]:
    target = logging.getLogger("app.workers.scheduler")
    handler = _ListHandler()
    previous_level = target.level
    previous_disabled = target.disabled
    target.addHandler(handler)
    target.setLevel(logging.INFO)
    # test_import_linter.py calls import-linter's CLI in-process, which runs
    # logging.config.dictConfig(...) without disable_existing_loggers=False --
    # the stdlib default disables every logger that already existed, this one
    # included, for the rest of the test session. Force it back on here so
    # this fixture works regardless of what ran earlier in the same session.
    target.disabled = False
    try:
        yield handler.messages
    finally:
        target.removeHandler(handler)
        target.setLevel(previous_level)
        target.disabled = previous_disabled


# --- cleanup_expired_sessions --------------------------------------------------


async def test_cleanup_expired_sessions_prunes_expired_rows(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user = await _seed_user(session)
    repo = SessionRepository(session)
    await repo.create(
        user_id=user.id,
        token_hash="keep",
        device_label=None,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=_future(),
    )
    await repo.create(
        user_id=user.id,
        token_hash="gone",
        device_label=None,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=_past(),
    )
    await session.commit()

    await cleanup_expired_sessions({})

    async with session_factory() as verify_session:
        verify_repo = SessionRepository(verify_session)
        assert await verify_repo.get_by_token_hash("keep") is not None
        assert await verify_repo.get_by_token_hash("gone") is None


# --- cleanup_expired_tokens -----------------------------------------------------


async def test_cleanup_expired_tokens_deletes_expired_across_all_three_tables(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    scheduler_log_messages: list[str],
) -> None:
    user = await _seed_user(session)
    repo_classes = [
        EmailVerificationTokenRepository,
        PasswordResetTokenRepository,
        MagicLinkTokenRepository,
    ]
    for repo_cls in repo_classes:
        repo = repo_cls(session)
        await repo.create(user_id=user.id, token_hash="keep", expires_at=_future())
        await repo.create(user_id=user.id, token_hash="gone", expires_at=_past())
    await session.commit()

    await cleanup_expired_tokens({})

    async with session_factory() as verify_session:
        for repo_cls in repo_classes:
            assert await repo_cls(verify_session).get_by_hash("keep") is not None
    logged = " ".join(scheduler_log_messages)
    assert "'email_verification': 1" in logged
    assert "'password_reset': 1" in logged
    assert "'magic_link': 1" in logged


# --- sync_expiring_trials -------------------------------------------------------


async def test_sync_expiring_trials_logs_count_within_horizon(
    session: AsyncSession, scheduler_log_messages: list[str]
) -> None:
    plan = await _seed_plan(session)
    subs = SubscriptionRepository(session)
    await subs.upsert(
        org_id=uuid4(),
        plan_id=plan.id,
        stripe_subscription_id="sub_inside",
        status="trialing",
        current_period_start=_past(48),
        current_period_end=_future(48),
        cancel_at_period_end=False,
        trial_end=_future(24),
    )
    await subs.upsert(
        org_id=uuid4(),
        plan_id=plan.id,
        stripe_subscription_id="sub_outside",
        status="trialing",
        current_period_start=_past(48),
        current_period_end=_future(48),
        cancel_at_period_end=False,
        trial_end=_future(24 * 30),
    )
    await session.commit()

    await sync_expiring_trials({})

    assert any("1 trial subscriptions expire within 3 days" in m for m in scheduler_log_messages)


# --- expire_data_export_urls ----------------------------------------------------


async def test_expire_data_export_urls_expires_only_ready_past_expiry(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user = await _seed_user(session)
    jobs = DataExportJobRepository(session)

    expired = await jobs.create(user_id=user.id)
    expired.status = "ready"
    expired.download_url = "https://example.com/export.zip"
    expired.expires_at = _past()

    not_yet = await jobs.create(user_id=user.id)
    not_yet.status = "ready"
    not_yet.download_url = "https://example.com/export2.zip"
    not_yet.expires_at = _future()

    pending = await jobs.create(user_id=user.id)
    pending.expires_at = _past()
    await session.commit()

    await expire_data_export_urls({})

    async with session_factory() as verify_session:
        verify_jobs = DataExportJobRepository(verify_session)

        refetched_expired = await verify_jobs.get_by_id(expired.id)
        assert refetched_expired is not None
        assert refetched_expired.status == "expired"
        assert refetched_expired.download_url is None

        refetched_not_yet = await verify_jobs.get_by_id(not_yet.id)
        assert refetched_not_yet is not None
        assert refetched_not_yet.status == "ready"

        refetched_pending = await verify_jobs.get_by_id(pending.id)
        assert refetched_pending is not None
        assert refetched_pending.status == "pending"


# --- with_distributed_lock ------------------------------------------------------


@pytest.fixture(scope="module")
def redis_container() -> "RedisContainer":
    with RedisContainer("redis:7-alpine") as container:
        yield container


@pytest.fixture
async def redis_client(redis_container: "RedisContainer") -> AsyncGenerator[Redis, None]:
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    client = Redis.from_url(f"redis://{host}:{port}/0")
    await client.flushdb()
    yield client
    await client.aclose()


async def test_distributed_lock_runs_func_when_unlocked(redis_client: Redis) -> None:
    calls = []

    async def job() -> None:
        calls.append(1)

    await with_distributed_lock(redis_client, "test-job", job)

    assert calls == [1]


async def test_distributed_lock_skips_when_already_held(redis_client: Redis) -> None:
    await redis_client.set("scheduler:lock:test-job", "1", nx=True, ex=300)
    calls = []

    async def job() -> None:
        calls.append(1)

    await with_distributed_lock(redis_client, "test-job", job)

    assert calls == []


async def test_distributed_lock_releases_after_success_allowing_next_tick(
    redis_client: Redis,
) -> None:
    calls = []

    async def job() -> None:
        calls.append(1)

    await with_distributed_lock(redis_client, "test-job", job)
    await with_distributed_lock(redis_client, "test-job", job)

    assert calls == [1, 1]


async def test_distributed_lock_releases_even_if_func_raises(redis_client: Redis) -> None:
    async def job() -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await with_distributed_lock(redis_client, "test-job", job)

    assert await redis_client.get("scheduler:lock:test-job") is None


# --- create_scheduler ------------------------------------------------------------


async def test_create_scheduler_registers_all_jobs_in_utc(redis_client: Redis) -> None:
    scheduler = create_scheduler(redis_client)

    jobs_by_id = {job.id: job for job in scheduler.get_jobs()}

    assert set(jobs_by_id) == {
        "cleanup_expired_sessions",
        "cleanup_expired_tokens",
        "sync_expiring_trials",
        "expire_data_export_urls",
        "notify_expiring_trials",
        "expire_trials_to_inactive",
    }
    assert "hour='*/6'" in repr(jobs_by_id["cleanup_expired_sessions"].trigger)
    assert "hour='8'" in repr(jobs_by_id["sync_expiring_trials"].trigger)
    assert "hour='9'" in repr(jobs_by_id["notify_expiring_trials"].trigger)
    assert "hour='10'" in repr(jobs_by_id["expire_trials_to_inactive"].trigger)
    assert "hour=" not in repr(jobs_by_id["cleanup_expired_tokens"].trigger)
    assert "hour=" not in repr(jobs_by_id["expire_data_export_urls"].trigger)
    for job in jobs_by_id.values():
        assert "timezone='UTC'" in repr(job.trigger)


async def test_create_scheduler_job_runs_the_underlying_function_under_the_lock(
    redis_client: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_cleanup_expired_sessions(ctx: dict[str, Any]) -> None:
        calls.append(ctx)

    monkeypatch.setattr(scheduler_module, "cleanup_expired_sessions", fake_cleanup_expired_sessions)
    scheduler = create_scheduler(redis_client)
    registered_job = scheduler.get_job("cleanup_expired_sessions")
    assert registered_job is not None

    await registered_job.func()

    assert calls == [{}]
