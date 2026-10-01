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
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from app.core.metrics import ACTIVE_SESSIONS_REDIS_KEY
from app.modules.auth.models import (
    User,
)
from app.modules.auth.repository import (
    EmailVerificationTokenRepository,
    MagicLinkTokenRepository,
    PasswordResetTokenRepository,
    SessionRepository,
)
from app.modules.billing.models import Plan
from app.modules.billing.repository import SubscriptionRepository
from app.modules.gdpr.repository import DataExportJobRepository
from app.modules.notifications.models import Notification
from app.modules.tenancy.models import Organization
from app.workers import scheduler as scheduler_module
from app.workers.scheduler import (
    cleanup_expired_sessions,
    cleanup_expired_tokens,
    create_scheduler,
    expire_data_export_urls,
    prune_old_notifications,
    refresh_active_sessions_gauge,
    sync_expiring_trials,
    with_distributed_lock,
)


def _past(hours: int = 1) -> datetime:
    return datetime.now(UTC) - timedelta(hours=hours)


def _future(hours: int = 1) -> datetime:
    return datetime.now(UTC) + timedelta(hours=hours)


@pytest.fixture(autouse=True)
def _use_test_engine(monkeypatch: pytest.MonkeyPatch, test_engine: AsyncEngine) -> None:
    # The job functions under test open their own session via the module-level
    # async_session_factory (they don't take a session param -- see
    # app.workers.jobs.encryption for the same shape) -- point that factory at
    # the test engine, same pattern as test_encryption_job.py.
    factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(scheduler_module, "async_session_factory", factory)


async def _seed_user(session: AsyncSession) -> User:
    user = User(email=f"{uuid4()}@example.com")
    session.add(user)
    await session.flush()
    return user


async def _seed_org(session: AsyncSession) -> Organization:
    org = Organization(name="Acme", slug=f"acme-{uuid4().hex[:12]}")
    session.add(org)
    await session.flush()
    return org


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


async def test_cleanup_expired_sessions_revokes_idle_rows_without_deleting_them(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The other half of session ageing.

    An idle-capped session is unusable but still inside its absolute cap, so
    it must be revoked here rather than deleted -- prune_expired stays the one
    place a row leaves the table. Before this, these accumulated until their
    thirtieth day and every reader that forgot the idle rule called them live.
    """
    user = await _seed_user(session)
    repo = SessionRepository(session)
    idle = await repo.create(
        user_id=user.id,
        token_hash="idle",
        device_label=None,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=_future(hours=24 * 20),
    )
    idle.last_seen_at = datetime.now(UTC) - timedelta(days=3)
    await repo.create(
        user_id=user.id,
        token_hash="active",
        device_label=None,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=_future(),
    )
    await session.commit()

    await cleanup_expired_sessions({})

    async with session_factory() as verify_session:
        verify_repo = SessionRepository(verify_session)
        still_there = await verify_repo.get_by_token_hash("idle")
        assert still_there is not None, "an idle session is revoked, not deleted"
        assert still_there.revoked_at is not None
        untouched = await verify_repo.get_by_token_hash("active")
        assert untouched is not None
        assert untouched.revoked_at is None


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


# --- prune_old_notifications -----------------------------------------------------


async def test_prune_old_notifications_deletes_only_rows_past_retention(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user = await _seed_user(session)
    org = await _seed_org(session)
    old = Notification(
        org_id=org.id,
        user_id=user.id,
        type="weekly_summary",
        title="old",
        body="old",
        channels=["in_app"],
        created_at=_past(hours=24 * 91),
    )
    recent = Notification(
        org_id=org.id,
        user_id=user.id,
        type="weekly_summary",
        title="recent",
        body="recent",
        channels=["in_app"],
        created_at=_past(hours=24 * 10),
    )
    session.add_all([old, recent])
    await session.commit()

    await prune_old_notifications({})

    async with session_factory() as verify_session:
        remaining = (await verify_session.get(Notification, old.id)) is not None
        assert not remaining
        assert (await verify_session.get(Notification, recent.id)) is not None


# --- refresh_active_sessions_gauge -----------------------------------------------


async def test_refresh_active_sessions_gauge_writes_active_count_to_redis(
    session: AsyncSession, redis_client: Redis
) -> None:
    user = await _seed_user(session)
    repo = SessionRepository(session)
    await repo.create(
        user_id=user.id,
        token_hash="active",
        device_label=None,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=_future(),
    )
    await repo.create(
        user_id=user.id,
        token_hash="expired",
        device_label=None,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=_past(),
    )
    await session.commit()

    await refresh_active_sessions_gauge(redis_client)

    assert await redis_client.get(ACTIVE_SESSIONS_REDIS_KEY) == b"1"


# --- with_distributed_lock ------------------------------------------------------


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
        "report_usage_to_stripe",
        "prune_old_notifications",
        # T-321: retention for webhook_delivery, and the safety net that
        # re-enqueues a due delivery whose deferred ARQ job was lost.
        "prune_webhook_deliveries",
        "sweep_due_webhook_deliveries",
        # The safety net for a webhook that never arrived: re-derives every
        # org's subscription from Stripe, which is the system of record for
        # what somebody is actually being charged.
        "reconcile_subscriptions",
        # The reconciliation half of StorageService.delete_file: the deferred
        # hard-delete is one Redis entry seven days out, and anything that
        # loses it loses the deletion silently.
        "sweep_orphaned_objects",
        # The other half of counting live presigns toward the storage quota:
        # without it an abandoned upload holds its org's allowance forever.
        "expire_abandoned_uploads",
        "refresh_active_sessions_gauge",
    }
    assert "hour='*/6'" in repr(jobs_by_id["cleanup_expired_sessions"].trigger)
    assert "hour='8'" in repr(jobs_by_id["sync_expiring_trials"].trigger)
    assert "hour='9'" in repr(jobs_by_id["notify_expiring_trials"].trigger)
    assert "hour='10'" in repr(jobs_by_id["expire_trials_to_inactive"].trigger)
    assert "hour=" not in repr(jobs_by_id["cleanup_expired_tokens"].trigger)
    assert "hour=" not in repr(jobs_by_id["expire_data_export_urls"].trigger)
    assert "minute='*/15'" in repr(jobs_by_id["report_usage_to_stripe"].trigger)
    # Hourly, on a minute of its own: the window between paying and being
    # upgraded is what this job bounds, so a daily sweep would leave a
    # customer effectively un-upgraded for a day.
    assert "minute='20'" in repr(jobs_by_id["reconcile_subscriptions"].trigger)
    assert "hour=" not in repr(jobs_by_id["reconcile_subscriptions"].trigger)
    assert "hour='2'" in repr(jobs_by_id["prune_old_notifications"].trigger)
    # An hour after the notification prune, so the two biggest delete sweeps
    # never contend for the same window.
    assert "hour='3'" in repr(jobs_by_id["prune_webhook_deliveries"].trigger)
    # Daily, and an hour of its own: every row it finds is already past its
    # grace period, so nothing is urgent -- but it shares the pool with the
    # rest of these, and stacking them on one hour is how a slow one starves
    # the next.
    assert "hour='4'" in repr(jobs_by_id["sweep_orphaned_objects"].trigger)
    # Every five minutes, not hourly: it only re-enqueues deliveries that are
    # already due, and the case it exists for (a Redis restart that lost the
    # deferred jobs) otherwise strands every pending delivery until the next
    # tick.
    assert "minute='*/5'" in repr(jobs_by_id["sweep_due_webhook_deliveries"].trigger)
    # Hourly, against an hour-long reservation window, so an abandoned
    # presign clears in one to two hours. Minute 40 because
    # reconcile_subscriptions holds 20 -- see its comment above.
    assert "minute='40'" in repr(jobs_by_id["expire_abandoned_uploads"].trigger)
    assert "hour=" not in repr(jobs_by_id["expire_abandoned_uploads"].trigger)
    assert "seconds=60" in repr(jobs_by_id["refresh_active_sessions_gauge"].trigger)
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

    # T-240: the ctx a scheduled job receives carries the same `redis` handle
    # ARQ puts in its own (app.workers.main), so a job that writes to the DB
    # can bust the caches that write invalidates -- expire_trials_to_inactive
    # downgrades a plan and needs exactly that.
    assert calls == [{"redis": redis_client}]


async def test_refresh_active_sessions_gauge_job_runs_under_the_lock(
    redis_client: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Redis] = []

    async def fake_refresh_active_sessions_gauge(client: Redis) -> None:
        calls.append(client)

    monkeypatch.setattr(
        scheduler_module, "refresh_active_sessions_gauge", fake_refresh_active_sessions_gauge
    )
    scheduler = create_scheduler(redis_client)
    registered_job = scheduler.get_job("refresh_active_sessions_gauge")
    assert registered_job is not None

    await registered_job.func()

    assert calls == [redis_client]
