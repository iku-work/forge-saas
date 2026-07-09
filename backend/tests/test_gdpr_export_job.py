"""
Verifies T-037's export_user_data ARQ job: collects every row the agents.md
spec lists (minus Notification/StoredFile, which don't exist until
T-101/T-110 -- see app.workers.jobs.gdpr's module docstring) into a single
data.json, zips it, uploads it via boto3 (mocked here -- no real S3/MinIO),
signs a 24h download URL, flips the DataExportJob to "ready", and emails the
link via send_email. Also a no-op when the user or the export job itself
doesn't exist.

Uses the same real-Postgres testcontainers pattern as test_gdpr_purge_job.py:
the job module's `async_session_factory` is monkeypatched to a session
factory bound to the test engine, and every assertion after calling the job
goes through a brand-new `verify_session` rather than the seeding session
(see test_gdpr_purge_job.py's docstring for why).
"""

import json
import zipfile
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from io import BytesIO
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel
from testcontainers.postgres import PostgresContainer

from app.modules.audit.models import AuditEvent
from app.modules.auth.models import OAuthAccount, User
from app.modules.auth.models import Session as SessionModel
from app.modules.auth.permissions import MemberRole
from app.modules.billing.models import Plan
from app.modules.gdpr.models import ConsentRecord, DataExportJob
from app.modules.tenancy.models import Membership, Organization
from app.workers.jobs import gdpr as gdpr_job_module
from app.workers.jobs.gdpr import _s3_client as _real_s3_client
from app.workers.jobs.gdpr import export_user_data

_DOWNLOAD_URL = "https://minio.local/forge-test/signed-url"

_TABLES = [
    User.__table__,
    SessionModel.__table__,
    OAuthAccount.__table__,
    Plan.__table__,
    Organization.__table__,
    Membership.__table__,
    ConsentRecord.__table__,
    DataExportJob.__table__,
    AuditEvent.__table__,
]


def _future(days: int = 1) -> datetime:
    return datetime.now(UTC) + timedelta(days=days)


@pytest.fixture(scope="module")
def postgres_container() -> "PostgresContainer":
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


# Function-scoped (not module-scoped): see test_gdpr_purge_job.py -- an
# asyncpg-backed engine can't be reused across pytest-asyncio's per-test loop.
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
    monkeypatch.setattr(gdpr_job_module, "async_session_factory", factory)


@pytest.fixture
def session_factory(test_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as s:
        yield s


@pytest.fixture(autouse=True)
def fake_s3_client(monkeypatch: pytest.MonkeyPatch) -> Mock:
    client = Mock()
    client.generate_presigned_url.return_value = _DOWNLOAD_URL
    monkeypatch.setattr(gdpr_job_module, "_s3_client", lambda: client)
    return client


@pytest.fixture(autouse=True)
def fake_send_email(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock()
    monkeypatch.setattr(gdpr_job_module, "send_email", mock)
    return mock


async def _make_user(session: AsyncSession, email: str) -> User:
    user = User(email=email, full_name="Export Me", hashed_password="argon2$dummy")
    session.add(user)
    await session.flush()
    return user


async def _make_org(session: AsyncSession) -> Organization:
    org = Organization(name="Acme", slug=f"acme-{uuid4()}")
    session.add(org)
    await session.flush()
    return org


async def _seed_export_footprint(session: AsyncSession, user: User, org: Organization) -> None:
    session.add(
        SessionModel(
            user_id=user.id,
            refresh_token_hash=f"sess-{user.id}",
            ip_address="127.0.0.1",
            user_agent="pytest",
            last_seen_at=datetime.now(UTC),
            expires_at=_future(),
            auth_time=datetime.now(UTC),
        )
    )
    session.add(OAuthAccount(user_id=user.id, provider="google", provider_user_id=str(user.id)))
    session.add(
        ConsentRecord(
            user_id=user.id,
            consent_type="marketing",
            granted=True,
            version="v1",
            ip_address="127.0.0.1",
            user_agent="pytest",
        )
    )
    session.add(
        AuditEvent(
            org_id=org.id, actor_id=user.id, action="user.login_success", resource_type="user"
        )
    )
    session.add(Membership(user_id=user.id, org_id=org.id, role=MemberRole.MEMBER))
    await session.flush()


def _read_zip_payload(zip_bytes: bytes) -> dict[str, object]:
    with zipfile.ZipFile(BytesIO(zip_bytes)) as archive:
        return json.loads(archive.read("data.json"))


# --- happy path ---------------------------------------------------------------


async def test_export_user_data_uploads_zip_with_every_expected_key(
    session: AsyncSession,
    fake_s3_client: Mock,
) -> None:
    org = await _make_org(session)
    user = await _make_user(session, "export-me@example.com")
    await _seed_export_footprint(session, user, org)
    job = DataExportJob(user_id=user.id)
    session.add(job)
    await session.commit()

    await export_user_data({}, user_id=user.id, job_id=job.id)

    fake_s3_client.put_object.assert_called_once()
    put_kwargs = fake_s3_client.put_object.call_args.kwargs
    assert put_kwargs["Bucket"] == "forge-test"
    assert put_kwargs["Key"] == f"data-exports/{user.id}/{job.id}.zip"

    payload = _read_zip_payload(put_kwargs["Body"])
    assert payload["user"]["email"] == "export-me@example.com"
    assert "hashed_password" not in payload["user"]
    assert "failed_login_count" not in payload["user"]
    assert len(payload["sessions"]) == 1
    assert len(payload["oauth_accounts"]) == 1
    assert len(payload["consent_records"]) == 1
    assert len(payload["audit_events"]) == 1
    assert len(payload["memberships"]) == 1


async def test_export_user_data_signs_a_24h_url(
    session: AsyncSession, fake_s3_client: Mock
) -> None:
    user = await _make_user(session, "signed@example.com")
    job = DataExportJob(user_id=user.id)
    session.add(job)
    await session.commit()

    await export_user_data({}, user_id=user.id, job_id=job.id)

    fake_s3_client.generate_presigned_url.assert_called_once()
    call = fake_s3_client.generate_presigned_url.call_args
    assert call.args[0] == "get_object"
    assert call.kwargs["ExpiresIn"] == 24 * 60 * 60


async def test_export_user_data_marks_job_ready_with_download_url(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user = await _make_user(session, "ready@example.com")
    job = DataExportJob(user_id=user.id)
    session.add(job)
    await session.commit()

    before = datetime.now(UTC)
    await export_user_data({}, user_id=user.id, job_id=job.id)

    async with session_factory() as verify_session:
        refetched = await verify_session.get(DataExportJob, job.id)
        assert refetched is not None
        assert refetched.status == "ready"
        assert refetched.download_url == _DOWNLOAD_URL
        assert refetched.expires_at is not None
        assert refetched.expires_at > before + timedelta(hours=23)


async def test_export_user_data_emails_the_download_link(
    session: AsyncSession, fake_send_email: AsyncMock
) -> None:
    user = await _make_user(session, "emailme@example.com")
    job = DataExportJob(user_id=user.id)
    session.add(job)
    await session.commit()

    await export_user_data({}, user_id=user.id, job_id=job.id)

    fake_send_email.assert_awaited_once()
    kwargs = fake_send_email.await_args.kwargs
    assert kwargs["to"] == "emailme@example.com"
    assert kwargs["template"] == "download_link"
    assert kwargs["context"]["download_url"] == _DOWNLOAD_URL


# --- guard clauses -------------------------------------------------------------


async def test_export_user_data_is_a_noop_for_unknown_user(
    fake_s3_client: Mock, fake_send_email: AsyncMock
) -> None:
    await export_user_data({}, user_id=uuid4(), job_id=uuid4())

    fake_s3_client.put_object.assert_not_called()
    fake_send_email.assert_not_awaited()


async def test_export_user_data_skips_upload_when_job_is_missing(
    session: AsyncSession, fake_s3_client: Mock, fake_send_email: AsyncMock
) -> None:
    user = await _make_user(session, "nojob@example.com")
    await session.commit()

    await export_user_data({}, user_id=user.id, job_id=uuid4())

    fake_s3_client.put_object.assert_not_called()
    fake_send_email.assert_not_awaited()


async def test_export_user_data_skips_upload_when_job_belongs_to_another_user(
    session: AsyncSession, fake_s3_client: Mock, fake_send_email: AsyncMock
) -> None:
    owner = await _make_user(session, "owner@example.com")
    other = await _make_user(session, "other@example.com")
    job = DataExportJob(user_id=other.id)
    session.add(job)
    await session.commit()

    await export_user_data({}, user_id=owner.id, job_id=job.id)

    fake_s3_client.put_object.assert_not_called()
    fake_send_email.assert_not_awaited()


async def test_export_user_data_skips_email_when_job_vanishes_after_upload(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    fake_send_email: AsyncMock,
) -> None:
    user = await _make_user(session, "vanishing@example.com")
    job = DataExportJob(user_id=user.id)
    session.add(job)
    await session.commit()

    # Simulate the job row disappearing during the (mocked, here synchronous)
    # upload -- e.g. a concurrent delete -- by deleting it as a side effect of
    # the upload step rather than mocking _s3_client directly.
    async def _upload_and_delete_job(*_args: object, **_kwargs: object) -> str:
        async with session_factory() as deleting_session:
            row = await deleting_session.get(DataExportJob, job.id)
            assert row is not None
            await deleting_session.delete(row)
            await deleting_session.commit()
        return _DOWNLOAD_URL

    monkeypatch.setattr(gdpr_job_module, "_upload_export_zip", _upload_and_delete_job)

    await export_user_data({}, user_id=user.id, job_id=job.id)

    fake_send_email.assert_not_awaited()


# --- _s3_client ----------------------------------------------------------------


def test_s3_client_is_configured_from_settings() -> None:
    # Bypasses the fake_s3_client autouse fixture by holding a reference to
    # the real function captured at import time (the fixture monkeypatches
    # the module attribute, not this already-bound reference) -- proves the
    # client is actually wired to STORAGE_BACKEND's AWS_* settings (.env.test:
    # MinIO at http://localhost:9000, region us-east-1).
    client = _real_s3_client()

    assert client.meta.region_name == "us-east-1"
    assert client.meta.endpoint_url == "http://localhost:9000"
