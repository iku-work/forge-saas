"""
Verifies T-113's virus_scan ARQ job: aborts when the file is missing or
already past "ready" (already scanned/deleted), the clean outcome (status
stays "ready", virus_scan_result="clean"), the infected outcome (status
"infected", the underlying object deleted from storage immediately, an
audit event, and a Notification(type="file_infected") fanned out to the
uploader -- including the case where the scanner reports infected with no
signature), and a scanner that raises ScannerError being retried up to 3x
(T-206's app.core.resilience `@resilient("virus_scanner")`, exercised here
rather than mocked) before the file is marked "failed" (also audit-logged)
-- plus the case where a transient failure on the first attempt is
recovered by a clean second attempt, and the case where the shared
"virus_scanner" breaker has actually tripped open (job re-queues via
arq.worker.Retry, file stays untouched rather than marked "failed").

Uses the same real-Postgres testcontainers pattern as test_gdpr_purge_job.py /
test_backfill_notification_preferences_job.py: virus_scan's own
`async_session_factory` is monkeypatched to a session factory bound to a
dedicated test engine (own tables: User, Plan, Organization, StoredFile,
Notification, AuditEvent -- Plan is needed only because Organization.plan_id
has a real FK to plan.id, same as test_notifications_router.py). The
StorageBackend and VirusScanner are both faked (monkeypatched onto the job
module directly) rather than hitting real S3/clamd -- the wire protocols for
each are already covered by test_storage_backend.py (T-111) and
test_storage_scanner.py (T-113) respectively; this file only verifies the
job's own orchestration.
"""

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest
from arq.worker import Retry
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from app.core.resilience import BREAKERS
from app.modules.audit.models import AuditEvent
from app.modules.auth.models import User
from app.modules.billing.models import Plan
from app.modules.notifications.models import Notification
from app.modules.storage.models import StoredFile
from app.modules.storage.scanner import ScannerError, ScanResult
from app.modules.tenancy.models import Organization
from app.workers.jobs import virus_scan as virus_scan_module
from app.workers.jobs.virus_scan import virus_scan

_TABLES = [
    User.__table__,
    Plan.__table__,
    Organization.__table__,
    StoredFile.__table__,
    Notification.__table__,
    AuditEvent.__table__,
]


@pytest.fixture(scope="module")
def postgres_container() -> "PostgresContainer":
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture(scope="module")
def redis_container() -> "RedisContainer":
    with RedisContainer("redis:7-alpine") as container:
        yield container


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
    factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(virus_scan_module, "async_session_factory", factory)


@pytest.fixture
def session_factory(test_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as s:
        yield s


@pytest.fixture
async def redis(redis_container: "RedisContainer") -> AsyncGenerator[Redis, None]:
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    client: Redis = Redis.from_url(f"redis://{host}:{port}/0")
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture
def ctx(redis: Redis) -> dict[str, Any]:
    return {"redis": redis}


@dataclass
class _FakeBackend:
    deleted: list[str] = field(default_factory=list)

    async def presign_download(
        self,
        key: str,
        expires_in: int = 3600,  # noqa: ARG002 -- fake doesn't model expiry
        *,
        filename: str | None = None,  # noqa: ARG002
    ) -> str:
        return f"https://fake.example/download/{key}"

    async def delete(self, key: str) -> None:
        self.deleted.append(key)


@dataclass
class _FakeScanner:
    outcomes: list[ScanResult | ScannerError]
    calls: int = 0

    async def scan(self, *, download_url: str, checksum_sha256: str | None) -> ScanResult:  # noqa: ARG002
        outcome = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        if isinstance(outcome, ScannerError):
            raise outcome
        return outcome


@pytest.fixture
def fake_backend(monkeypatch: pytest.MonkeyPatch) -> _FakeBackend:
    backend = _FakeBackend()
    monkeypatch.setattr(virus_scan_module, "get_storage_backend", lambda: backend)
    return backend


def _install_scanner(
    monkeypatch: pytest.MonkeyPatch, *outcomes: ScanResult | ScannerError
) -> _FakeScanner:
    scanner = _FakeScanner(outcomes=list(outcomes))
    monkeypatch.setattr(virus_scan_module, "get_virus_scanner", lambda: scanner)
    return scanner


async def _make_org(session: AsyncSession) -> Organization:
    org = Organization(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    session.add(org)
    await session.flush()
    return org


async def _make_user(session: AsyncSession, email: str | None = None) -> User:
    user = User(email=email or f"user-{uuid4()}@example.com")
    session.add(user)
    await session.flush()
    return user


async def _make_stored_file(
    session: AsyncSession,
    *,
    org: Organization,
    user: User,
    status: str = "ready",
    checksum_sha256: str | None = "deadbeef",
) -> StoredFile:
    stored_file = StoredFile(
        org_id=org.id,
        uploaded_by=user.id,
        key=f"{org.id}/default/{uuid4()}/report.pdf",
        filename="report.pdf",
        content_type="application/pdf",
        size_bytes=1024,
        status=status,
        checksum_sha256=checksum_sha256,
    )
    session.add(stored_file)
    await session.commit()
    return stored_file


async def _seed(
    session: AsyncSession, *, status: str = "ready", checksum_sha256: str | None = "deadbeef"
) -> StoredFile:
    org = await _make_org(session)
    user = await _make_user(session)
    return await _make_stored_file(
        session, org=org, user=user, status=status, checksum_sha256=checksum_sha256
    )


# --- abort conditions ----------------------------------------------------------------------


async def test_skips_unknown_file(ctx: dict[str, Any], fake_backend: _FakeBackend) -> None:
    await virus_scan(ctx, file_id=uuid4())

    assert fake_backend.deleted == []


async def test_skips_file_not_in_ready_status(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    ctx: dict[str, Any],
    fake_backend: _FakeBackend,  # noqa: ARG001
) -> None:
    stored_file = await _seed(session, status="pending")

    await virus_scan(ctx, file_id=stored_file.id)

    async with session_factory() as verify_session:
        refreshed = await verify_session.get(StoredFile, stored_file.id)
        assert refreshed is not None
        assert refreshed.status == "pending"
        assert refreshed.virus_scan_result is None


# --- clean outcome ---------------------------------------------------------------------------


async def test_clean_scan_leaves_file_ready_with_clean_result(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    ctx: dict[str, Any],
    fake_backend: _FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored_file = await _seed(session)
    _install_scanner(monkeypatch, ScanResult(clean=True))

    await virus_scan(ctx, file_id=stored_file.id)

    async with session_factory() as verify_session:
        refreshed = await verify_session.get(StoredFile, stored_file.id)
        assert refreshed is not None
        assert refreshed.status == "ready"
        assert refreshed.virus_scan_result == "clean"
    assert fake_backend.deleted == []


# --- infected outcome -------------------------------------------------------------------------


async def test_infected_scan_marks_infected_deletes_object_and_notifies(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    ctx: dict[str, Any],
    fake_backend: _FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org = await _make_org(session)
    user = await _make_user(session)
    stored_file = await _make_stored_file(session, org=org, user=user)
    _install_scanner(monkeypatch, ScanResult(clean=False, signature="Eicar-Test-Signature"))

    await virus_scan(ctx, file_id=stored_file.id)

    async with session_factory() as verify_session:
        refreshed = await verify_session.get(StoredFile, stored_file.id)
        assert refreshed is not None
        assert refreshed.status == "infected"
        assert refreshed.virus_scan_result == "Eicar-Test-Signature"

        audit_rows = (
            (
                await verify_session.execute(
                    select(AuditEvent).where(AuditEvent.resource_id == str(stored_file.id))
                )
            )
            .scalars()
            .all()
        )
        assert any(row.action == "file.infected" for row in audit_rows)

        notifications = (
            (
                await verify_session.execute(
                    select(Notification).where(Notification.user_id == user.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(notifications) == 1
        assert notifications[0].type == "file_infected"

    assert fake_backend.deleted == [stored_file.key]


async def test_infected_scan_without_signature_uses_generic_label(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    ctx: dict[str, Any],
    fake_backend: _FakeBackend,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored_file = await _seed(session)
    _install_scanner(monkeypatch, ScanResult(clean=False, signature=None))

    await virus_scan(ctx, file_id=stored_file.id)

    async with session_factory() as verify_session:
        refreshed = await verify_session.get(StoredFile, stored_file.id)
        assert refreshed is not None
        assert refreshed.status == "infected"
        assert refreshed.virus_scan_result == "infected"


# --- scanner failure / retries ----------------------------------------------------------------


async def test_scanner_failure_retries_three_times_then_marks_failed(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    ctx: dict[str, Any],
    fake_backend: _FakeBackend,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored_file = await _seed(session)
    scanner = _install_scanner(
        monkeypatch,
        ScannerError("boom"),
        ScannerError("boom"),
        ScannerError("boom"),
    )

    await virus_scan(ctx, file_id=stored_file.id)

    assert scanner.calls == 3
    async with session_factory() as verify_session:
        refreshed = await verify_session.get(StoredFile, stored_file.id)
        assert refreshed is not None
        assert refreshed.status == "failed"
        assert refreshed.virus_scan_result is None

        audit_rows = (
            (
                await verify_session.execute(
                    select(AuditEvent).where(AuditEvent.resource_id == str(stored_file.id))
                )
            )
            .scalars()
            .all()
        )
        assert any(row.action == "file.scan_failed" for row in audit_rows)


async def test_scanner_recovers_after_one_transient_failure(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    ctx: dict[str, Any],
    fake_backend: _FakeBackend,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored_file = await _seed(session)
    scanner = _install_scanner(monkeypatch, ScannerError("transient"), ScanResult(clean=True))

    await virus_scan(ctx, file_id=stored_file.id)

    assert scanner.calls == 2
    async with session_factory() as verify_session:
        refreshed = await verify_session.get(StoredFile, stored_file.id)
        assert refreshed is not None
        assert refreshed.status == "ready"
        assert refreshed.virus_scan_result == "clean"


# --- T-206: breaker-open re-queues the job instead of marking the file failed -----------------


async def test_breaker_open_requeues_job_and_leaves_file_untouched(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    ctx: dict[str, Any],
    fake_backend: _FakeBackend,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 5 separate files, each failing all 3 of their own scan attempts --
    # exactly enough real ScannerError failures to trip the shared
    # "virus_scanner" breaker (fail_max=5), one failure per file/job run.
    for _ in range(5):
        stored_file = await _seed(session)
        _install_scanner(
            monkeypatch, ScannerError("boom"), ScannerError("boom"), ScannerError("boom")
        )
        await virus_scan(ctx, file_id=stored_file.id)
    assert BREAKERS["virus_scanner"].current_state == "open"

    last_file = await _seed(session)
    scanner = _install_scanner(monkeypatch, ScannerError("boom"))

    with pytest.raises(Retry) as exc_info:
        await virus_scan(ctx, file_id=last_file.id)

    # The breaker rejected the call outright -- the scanner was never hit.
    assert scanner.calls == 0
    async with session_factory() as verify_session:
        refreshed = await verify_session.get(StoredFile, last_file.id)
        assert refreshed is not None
        assert refreshed.status == "ready"  # untouched -- neither "failed" nor "infected"
        assert refreshed.virus_scan_result is None
    assert exc_info.value.defer_score == virus_scan_module._VIRUS_SCANNER_RETRY_DEFER_SECONDS * 1000
