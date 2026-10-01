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

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import httpx
import pytest
from arq.worker import Retry
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from app.core.resilience import BREAKERS
from app.models._mixins import utcnow
from app.modules.audit.models import AuditEvent
from app.modules.auth.models import User
from app.modules.notifications.models import Notification
from app.modules.storage.keys import upload_key
from app.modules.storage.models import StoredFile
from app.modules.storage.scanner import BytesSource, ScannerError, ScanResult, ScanSource
from app.modules.tenancy.models import Organization
from app.workers.jobs import virus_scan as virus_scan_module
from app.workers.jobs.virus_scan import virus_scan


@pytest.fixture(autouse=True)
def _use_test_engine(monkeypatch: pytest.MonkeyPatch, test_engine: AsyncEngine) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(virus_scan_module, "async_session_factory", factory)


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
    # What the job actually handed over, so a test can assert the scanner saw
    # the object's real bytes and their real digest rather than the row's.
    seen_content: list[bytes] = field(default_factory=list)
    seen_checksums: list[str] = field(default_factory=list)

    # Called while the scanner "runs", so a test can look at the world from
    # inside the scan (e.g. which connections are checked out).
    during_scan: Callable[[], None] | None = None

    async def scan(self, source: ScanSource) -> ScanResult:
        if self.during_scan is not None:
            self.during_scan()
        content = b"".join([chunk async for chunk in source.chunks()])
        self.seen_content.append(content)
        self.seen_checksums.append(source.sha256 or "")
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


# The object's bytes and their real digest. The row is seeded with a
# *different* checksum ("deadbeef") on purpose, so any test asserting on
# _OBJECT_SHA256 is proving the job hashed what it fetched rather than
# trusting the column -- which is the whole point of moving the digest here.
_OBJECT_BYTES = b"%PDF-1.7 the object's actual bytes"
_OBJECT_SHA256 = hashlib.sha256(_OBJECT_BYTES).hexdigest()


@pytest.fixture(autouse=True)
def fake_download(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for the object store the job streams from.

    Patched at `_open_source` rather than at the httpx layer: this file's
    first half covers the job's orchestration, and the streaming and size
    limit of the real source have their own tests below, driven through
    httpx.MockTransport.
    """
    monkeypatch.setattr(
        virus_scan_module,
        "_open_source",
        lambda backend, key: BytesSource(_OBJECT_BYTES, chunk_size=8),  # noqa: ARG005 -- _open_source's signature
    )


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


# --- the digest ------------------------------------------------------------------------------


async def test_a_clean_scan_records_the_digest_of_the_bytes_it_scanned(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    ctx: dict[str, Any],
    fake_backend: _FakeBackend,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The row arrives carrying "deadbeef" -- what confirm_upload used to put
    # there from the object's ETag, which is not a SHA-256 at all. After the
    # scan the column holds the digest of the bytes that were actually read.
    stored_file = await _seed(session, checksum_sha256="deadbeef")
    _install_scanner(monkeypatch, ScanResult(clean=True))

    await virus_scan(ctx, file_id=stored_file.id)

    async with session_factory() as verify_session:
        refreshed = await verify_session.get(StoredFile, stored_file.id)
        assert refreshed is not None
        assert refreshed.checksum_sha256 == _OBJECT_SHA256


async def test_the_scanner_is_given_the_fetched_bytes_and_their_own_digest(
    session: AsyncSession,
    ctx: dict[str, Any],
    fake_backend: _FakeBackend,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # VirusTotalScanner looks a file up *by* this digest, so handing it the
    # row's stale value meant querying a hash that could never match.
    stored_file = await _seed(session, checksum_sha256="deadbeef")
    scanner = _install_scanner(monkeypatch, ScanResult(clean=True))

    await virus_scan(ctx, file_id=stored_file.id)

    assert scanner.seen_content == [_OBJECT_BYTES]
    assert scanner.seen_checksums == [_OBJECT_SHA256]


async def test_an_infected_file_keeps_the_digest_of_what_was_found(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    ctx: dict[str, Any],
    fake_backend: _FakeBackend,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The object is deleted at this point, so the digest is the only record
    # left of what was actually uploaded.
    stored_file = await _seed(session, checksum_sha256=None)
    _install_scanner(monkeypatch, ScanResult(clean=False, signature="Eicar-Test-Signature"))

    await virus_scan(ctx, file_id=stored_file.id)

    async with session_factory() as verify_session:
        refreshed = await verify_session.get(StoredFile, stored_file.id)
        assert refreshed is not None
        assert refreshed.status == "infected"
        assert refreshed.checksum_sha256 == _OBJECT_SHA256


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

    # Every copy, including anything left at the upload key (review R1).
    assert fake_backend.deleted == [stored_file.key, upload_key(stored_file.key)]


async def test_a_failed_delete_is_finished_by_the_retry_without_losing_the_verdict(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    ctx: dict[str, Any],
    fake_backend: _FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The verdict commits before the object is deleted, so a delete that
    fails leaves an infected row -- refused for download -- that the retry
    cleans up, instead of a retry that can no longer read the object and
    records a scanner failure in place of the malware."""
    org = await _make_org(session)
    user = await _make_user(session)
    stored_file = await _make_stored_file(session, org=org, user=user)
    scanner = _install_scanner(monkeypatch, ScanResult(clean=False, signature="Eicar"))
    real_delete = fake_backend.delete
    failures = [OSError("object store hiccup")]

    async def _flaky_delete(key: str) -> None:
        if failures:
            raise failures.pop()
        await real_delete(key)

    fake_backend.delete = _flaky_delete  # type: ignore[method-assign]

    with pytest.raises(OSError, match="hiccup"):
        await virus_scan(ctx, file_id=stored_file.id)
    await virus_scan(ctx, file_id=stored_file.id)

    assert scanner.calls == 1  # the retry did not scan again
    assert fake_backend.deleted == [stored_file.key, upload_key(stored_file.key)]
    async with session_factory() as verify_session:
        refreshed = await verify_session.get(StoredFile, stored_file.id)
        assert refreshed is not None
        assert refreshed.status == "infected"
        assert refreshed.virus_scan_result == "Eicar"
        audit_actions = (
            (
                await verify_session.execute(
                    select(AuditEvent.action).where(AuditEvent.resource_id == str(stored_file.id))
                )
            )
            .scalars()
            .all()
        )
        assert audit_actions.count("file.infected") == 1
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


# --- fetching the object ----------------------------------------------------------------------
#
# These drive the real `_fetch`/`_download` (the autouse fake_download
# fixture is overridden or bypassed), because the memory bound they enforce
# is the point: `response.content` on an unbounded presigned download used to
# live inside ClamAVScanner, where one oversized upload could exhaust a
# worker shared by 20 jobs.


async def test_an_oversized_file_is_refused_before_it_is_fetched(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    ctx: dict[str, Any],
    fake_backend: _FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org = await _make_org(session)
    user = await _make_user(session)
    stored_file = await _make_stored_file(session, org=org, user=user)
    stored_file.size_bytes = virus_scan_module._MAX_SCAN_BYTES + 1
    await session.commit()

    fetched: list[str] = []

    def _never(backend: object, key: str) -> BytesSource:  # noqa: ARG001 -- _open_source's signature
        fetched.append(key)
        return BytesSource(b"")

    monkeypatch.setattr(virus_scan_module, "_open_source", _never)
    scanner = _install_scanner(monkeypatch, ScanResult(clean=True))

    await virus_scan(ctx, file_id=stored_file.id)

    assert fetched == []  # declined on the row's size alone
    assert scanner.calls == 0
    async with session_factory() as verify_session:
        refreshed = await verify_session.get(StoredFile, stored_file.id)
        assert refreshed is not None
        # "failed", never "clean": a file too big to scan must not become a
        # file that skips scanning.
        assert refreshed.status == "failed"
        assert refreshed.virus_scan_result is None
    assert fake_backend.deleted == []


def _real_source(handler: Callable[[httpx.Request], httpx.Response]) -> ScanSource:
    return virus_scan_module._ObjectSource(
        _FakeBackend(), "org/default/f/report.pdf", transport=httpx.MockTransport(handler)
    )


async def _read_all(source: ScanSource) -> bytes:
    return b"".join([chunk async for chunk in source.chunks()])


async def test_the_download_stops_reading_once_it_passes_the_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The row's claimed size and the object's real size are different facts,
    # so the streaming loop is what ends an oversized read -- not the
    # row-level check that runs before it.
    monkeypatch.setattr(virus_scan_module, "_MAX_SCAN_BYTES", 1024)

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001 -- canned
        return httpx.Response(200, content=b"x" * 2048)

    with pytest.raises(virus_scan_module.ScanTooLargeError):
        await _read_all(_real_source(handler))


async def test_a_download_failure_is_a_scanner_error_not_a_verdict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001 -- canned
        raise httpx.ConnectError("connection refused")

    with pytest.raises(ScannerError):
        await _read_all(_real_source(handler))


async def test_a_download_http_error_is_a_scanner_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001 -- canned
        return httpx.Response(503)

    with pytest.raises(ScannerError):
        await _read_all(_real_source(handler))


async def test_the_stream_is_the_object_and_its_digest_is_of_the_stream() -> None:
    body = bytes(i % 256 for i in range(200_000))

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001 -- canned
        return httpx.Response(200, content=body)

    source = _real_source(handler)
    assert source.sha256 is None  # nothing read yet

    assert await _read_all(source) == body
    assert source.sha256 == hashlib.sha256(body).hexdigest()


async def test_each_pass_reads_the_object_again() -> None:
    """A retried scan re-streams rather than replaying a buffer (review R4)."""
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, content=b"bytes")

    source = _real_source(handler)
    await _read_all(source)
    await _read_all(source)

    assert len(requests) == 2


# --- review R4: nothing held across the scan -------------------------------------------------


async def test_no_database_connection_is_held_while_scanning(
    session: AsyncSession,
    test_engine: AsyncEngine,
    ctx: dict[str, Any],
    fake_backend: _FakeBackend,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under pgbouncer transaction pooling an open transaction pins a server
    connection, and this job used to hold one for the whole download and
    scan. The scanner checks the pool from inside the scan."""
    stored_file = await _seed(session)
    await session.close()  # the test's own handle, out of the count
    checked_out: list[int] = []
    scanner = _install_scanner(monkeypatch, ScanResult(clean=True))
    scanner.during_scan = lambda: checked_out.append(test_engine.pool.checkedout())

    await virus_scan(ctx, file_id=stored_file.id)

    assert checked_out == [0]


async def test_a_file_deleted_during_the_scan_keeps_its_deletion(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    ctx: dict[str, Any],
    fake_backend: _FakeBackend,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing holds the row during the scan, so the verdict is conditional:
    a file soft-deleted in the meantime is not revived as "clean"."""
    stored_file = await _seed(session)
    file_id = stored_file.id
    scanner = _install_scanner(monkeypatch, ScanResult(clean=True))
    original_scan = scanner.scan

    async def _scan_then_delete(source: ScanSource) -> ScanResult:
        result = await original_scan(source)
        async with session_factory() as other:
            row = await other.get(StoredFile, file_id)
            assert row is not None
            row.deleted_at = utcnow()
            await other.commit()
        return result

    scanner.scan = _scan_then_delete  # type: ignore[method-assign]

    await virus_scan(ctx, file_id=file_id)

    async with session_factory() as verify_session:
        refreshed = await verify_session.get(StoredFile, file_id)
        assert refreshed is not None
        assert refreshed.deleted_at is not None
        assert refreshed.virus_scan_result is None
