"""
Verifies T-112's StorageService: content-type allowlist enforcement
(per-bucket overrides included), the two plan-quota checks (per-file
max_upload_mb cap, org-wide storage_gb cap), confirm_upload's three
orchestration-layer security requirements (existence via head(), size
tolerance, idempotent re-confirm), the virus_scan job enqueue, delete_file's
soft-delete + deferred hard-delete enqueue, and get_download_url's scan gate.

The last of those is the reason the section at the bottom of this file
exists. get_download_url had no tests at all, and no check that the file it
was signing had been scanned -- so an object was downloadable from the
moment confirm_upload marked it "ready", which happens *before* the scan job
is even enqueued, and stayed downloadable if that scan later failed.

The real S3-compatible wire protocol (presigned POST conditions, expiry,
content-length-range, etc.) is T-111's own concern, fully covered by
test_storage_backend.py against a real MinIO container -- this file fakes
StorageBackend entirely so these orchestration-layer checks run fast and
without a third testcontainer.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    PlanLimitError,
    UnprocessableEntityError,
)
from app.modules.auth.repository import UserRepository
from app.modules.billing.dependencies import OrgSubscription
from app.modules.billing.models import Plan
from app.modules.storage import service as service_module
from app.modules.storage.backend import PresignedUpload
from app.modules.storage.repository import StoredFileRepository
from app.modules.storage.service import StorageService
from app.modules.tenancy.repository import OrganizationRepository

# T-291: this file needs file_storage enabled -- its routes, its tables, or
# both. CI's module-matrix job deselects it in the combination that turns
# every optional module off.
pytestmark = pytest.mark.module_dependent


@dataclass
class _FakeStorageBackend:
    uploaded: dict[str, dict[str, Any]] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)
    # T-225: content-sniffing needs *some* bytes at confirm time -- real PDF
    # magic bytes by default, since every pre-existing test in this file
    # declares content_type="application/pdf". Override per-key to exercise
    # a declared/actual mismatch.
    content: dict[str, bytes] = field(default_factory=dict)

    async def presign_upload(
        self,
        key: str,
        content_type: str,
        max_bytes: int,  # noqa: ARG002 -- fake ignores the byte cap, real enforcement is T-111's
        expires_in: int = 600,
    ) -> PresignedUpload:
        return PresignedUpload(
            url=f"https://fake.example/{key}",
            fields={"Content-Type": content_type},
            key=key,
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
        )

    async def presign_download(
        self,
        key: str,
        expires_in: int = 3600,  # noqa: ARG002 -- fake doesn't model expiry
        *,
        filename: str | None = None,  # noqa: ARG002 -- fake doesn't model Content-Disposition
    ) -> str:
        return f"https://fake.example/download/{key}"

    async def head(self, key: str) -> dict[str, Any] | None:
        return self.uploaded.get(key)

    async def read_prefix(self, key: str, num_bytes: int) -> bytes:  # noqa: ARG002 -- fake ignores the byte cap
        return self.content.get(key, b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.uploaded.pop(key, None)

    async def exists(self, key: str) -> bool:
        return key in self.uploaded


@pytest.fixture
def fake_backend(monkeypatch: pytest.MonkeyPatch) -> _FakeStorageBackend:
    backend = _FakeStorageBackend()
    monkeypatch.setattr(service_module, "get_storage_backend", lambda: backend)
    return backend


@pytest.fixture
def redis(_redis_client: Redis) -> Redis:
    return _redis_client


@pytest.fixture
async def org(session: AsyncSession):
    return await OrganizationRepository(session).create(name="Acme", slug=f"acme-{uuid4().hex[:8]}")


@pytest.fixture
async def user_id(session: AsyncSession):
    user = await UserRepository(session).create(email=f"uploader-{uuid4()}@example.com")
    return user.id


def _org_sub(org, **features: int) -> OrgSubscription:
    plan = Plan(
        name="Pro",
        slug=f"pro-{uuid4().hex[:8]}",
        stripe_price_id_monthly="price_m",
        stripe_price_id_yearly="price_y",
        price_monthly=2900,
        price_yearly=29000,
        features=features,
    )
    return OrgSubscription(org=org, subscription=None, plan=plan)


def _service(session: AsyncSession, redis: Redis, org) -> StorageService:
    return StorageService(session, org.id, redis)


# --- presign_upload: content-type allowlist ---------------------------------------------


@pytest.mark.usefixtures("fake_backend")
async def test_presign_upload_rejects_disallowed_content_type(
    session: AsyncSession, redis: Redis, org, user_id
) -> None:
    service = _service(session, redis, org)

    with pytest.raises(UnprocessableEntityError):
        await service.presign_upload(
            user_id,
            filename="malware.exe",
            content_type="application/x-executable",
            size_bytes=100,
            org_sub=None,
        )


@pytest.mark.usefixtures("fake_backend")
async def test_presign_upload_avatars_bucket_has_stricter_allowlist(
    session: AsyncSession, redis: Redis, org, user_id
) -> None:
    service = _service(session, redis, org)

    with pytest.raises(UnprocessableEntityError):
        await service.presign_upload(
            user_id,
            filename="doc.pdf",
            content_type="application/pdf",
            size_bytes=100,
            bucket="avatars",
            org_sub=None,
        )


@pytest.mark.usefixtures("fake_backend")
async def test_presign_upload_allows_image_in_avatars_bucket(
    session: AsyncSession, redis: Redis, org, user_id
) -> None:
    service = _service(session, redis, org)

    result = await service.presign_upload(
        user_id,
        filename="me.png",
        content_type="image/png",
        size_bytes=100,
        bucket="avatars",
        org_sub=None,
    )

    assert result["key"].startswith(f"{org.id}/avatars/")


# --- presign_upload: server-generated keys / filename is display-only -------------------


@pytest.mark.usefixtures("fake_backend")
async def test_presign_upload_key_is_server_generated_with_uuid_and_org_prefix(
    session: AsyncSession, redis: Redis, org, user_id
) -> None:
    service = _service(session, redis, org)

    result = await service.presign_upload(
        user_id,
        filename="report.pdf",
        content_type="application/pdf",
        size_bytes=100,
        org_sub=None,
    )

    parts = result["key"].split("/")
    assert parts[0] == str(org.id)
    assert parts[1] == "default"
    assert len(parts[2]) == 36  # uuid4 hex-with-dashes length
    assert parts[3] == "report.pdf"


@pytest.mark.usefixtures("fake_backend")
async def test_presign_upload_two_users_same_filename_get_different_keys(
    session: AsyncSession, redis: Redis, org, user_id
) -> None:
    service = _service(session, redis, org)
    other_user = await UserRepository(session).create(email=f"other-{uuid4()}@example.com")

    first = await service.presign_upload(
        user_id, filename="report.pdf", content_type="application/pdf", size_bytes=100, org_sub=None
    )
    second = await service.presign_upload(
        other_user.id,
        filename="report.pdf",
        content_type="application/pdf",
        size_bytes=100,
        org_sub=None,
    )

    assert first["key"] != second["key"]


# --- presign_upload: plan quota ----------------------------------------------------------


@pytest.mark.usefixtures("fake_backend")
async def test_presign_upload_no_quota_check_when_org_sub_is_none(
    session: AsyncSession, redis: Redis, org, user_id
) -> None:
    service = _service(session, redis, org)

    result = await service.presign_upload(
        user_id,
        filename="big.pdf",
        content_type="application/pdf",
        size_bytes=1_500_000_000,
        org_sub=None,
    )

    assert result["file_id"] is not None


@pytest.mark.usefixtures("fake_backend")
async def test_presign_upload_rejects_oversized_file_per_plan_cap(
    session: AsyncSession, redis: Redis, org, user_id
) -> None:
    service = _service(session, redis, org)
    org_sub = _org_sub(org, max_upload_mb=1, storage_gb=10)

    with pytest.raises(PlanLimitError):
        await service.presign_upload(
            user_id,
            filename="big.pdf",
            content_type="application/pdf",
            size_bytes=2_000_000,
            org_sub=org_sub,
        )


@pytest.mark.usefixtures("fake_backend")
async def test_presign_upload_allows_file_within_plan_cap(
    session: AsyncSession, redis: Redis, org, user_id
) -> None:
    service = _service(session, redis, org)
    org_sub = _org_sub(org, max_upload_mb=5, storage_gb=10)

    result = await service.presign_upload(
        user_id,
        filename="ok.pdf",
        content_type="application/pdf",
        size_bytes=1_000_000,
        org_sub=org_sub,
    )

    assert result["file_id"] is not None


@pytest.mark.usefixtures("fake_backend")
async def test_presign_upload_rejects_when_file_count_quota_would_be_exceeded(
    session: AsyncSession, redis: Redis, org, user_id
) -> None:
    service = _service(session, redis, org)
    repo = StoredFileRepository(session, org.id, redis)
    await repo.create(
        uploaded_by=user_id,
        key="existing",
        filename="existing.pdf",
        content_type="application/pdf",
        size_bytes=100,
        bucket="default",
        status="ready",
    )
    org_sub = _org_sub(org, max_files=1)

    with pytest.raises(PlanLimitError):
        await service.presign_upload(
            user_id,
            filename="one-too-many.pdf",
            content_type="application/pdf",
            size_bytes=100,
            org_sub=org_sub,
        )


@pytest.mark.usefixtures("fake_backend")
async def test_presign_upload_allows_file_within_file_count_quota(
    session: AsyncSession, redis: Redis, org, user_id
) -> None:
    service = _service(session, redis, org)
    org_sub = _org_sub(org, max_files=5)

    result = await service.presign_upload(
        user_id, filename="ok.pdf", content_type="application/pdf", size_bytes=100, org_sub=org_sub
    )

    assert result["file_id"] is not None


@pytest.mark.usefixtures("fake_backend")
async def test_presign_upload_rejects_when_org_quota_would_be_exceeded(
    session: AsyncSession, redis: Redis, org, user_id
) -> None:
    service = _service(session, redis, org)
    repo = StoredFileRepository(session, org.id, redis)
    await repo.create(
        uploaded_by=user_id,
        key="existing",
        filename="existing.pdf",
        content_type="application/pdf",
        size_bytes=900_000_000,
        bucket="default",
        status="ready",
    )
    org_sub = _org_sub(org, max_upload_mb=1000, storage_gb=1)

    with pytest.raises(PlanLimitError):
        await service.presign_upload(
            user_id,
            filename="more.pdf",
            content_type="application/pdf",
            size_bytes=1_000_000_000,
            org_sub=org_sub,
        )


# --- confirm_upload ------------------------------------------------------------------------


@pytest.mark.usefixtures("fake_backend")
async def test_confirm_upload_rejects_when_nothing_was_uploaded(
    session: AsyncSession, redis: Redis, org, user_id
) -> None:
    service = _service(session, redis, org)
    presigned = await service.presign_upload(
        user_id, filename="f.pdf", content_type="application/pdf", size_bytes=1024, org_sub=None
    )
    await session.commit()

    with pytest.raises(UnprocessableEntityError):
        await service.confirm_upload(user_id, presigned["file_id"])

    repo = StoredFileRepository(session, org.id, redis)
    stored = await repo.get_or_raise(presigned["file_id"])
    assert stored.status == "pending"


async def test_confirm_upload_rejects_size_mismatch_and_deletes_object(
    session: AsyncSession, redis: Redis, org, user_id, fake_backend
) -> None:
    service = _service(session, redis, org)
    presigned = await service.presign_upload(
        user_id, filename="f.pdf", content_type="application/pdf", size_bytes=1024, org_sub=None
    )
    await session.commit()
    fake_backend.uploaded[presigned["key"]] = {"ContentLength": 1024 + 2048, "ETag": '"abc"'}

    with pytest.raises(UnprocessableEntityError):
        await service.confirm_upload(user_id, presigned["file_id"])

    assert presigned["key"] in fake_backend.deleted


async def test_confirm_upload_happy_path_sets_ready_and_enqueues_scan(  # noqa: PLR0913 -- enqueued_jobs on top of the usual fixture set
    session: AsyncSession, redis: Redis, org, user_id, fake_backend, enqueued_jobs
) -> None:
    service = _service(session, redis, org)
    presigned = await service.presign_upload(
        user_id, filename="f.pdf", content_type="application/pdf", size_bytes=1024, org_sub=None
    )
    await session.commit()
    fake_backend.uploaded[presigned["key"]] = {"ContentLength": 1024, "ETag": '"deadbeef"'}

    updated = await service.confirm_upload(user_id, presigned["file_id"])

    assert updated.status == "ready"
    assert ("virus_scan", {"file_id": updated.id}) in enqueued_jobs


async def test_confirm_upload_does_not_put_the_etag_in_the_sha256_column(  # noqa: PLR0913 -- matches the fixture set above
    session: AsyncSession,
    redis: Redis,
    org,
    user_id,
    fake_backend,
    enqueued_jobs,  # noqa: ARG001
) -> None:
    # It used to. An ETag is not a SHA-256 -- for a multipart upload it is not
    # even an MD5 of the content -- so the column named one thing and held
    # another, and the hash-lookup scanner it fed could never match. The
    # digest is now the scan job's to write, from the bytes it reads.
    service = _service(session, redis, org)
    presigned = await service.presign_upload(
        user_id, filename="f.pdf", content_type="application/pdf", size_bytes=1024, org_sub=None
    )
    await session.commit()
    fake_backend.uploaded[presigned["key"]] = {"ContentLength": 1024, "ETag": '"deadbeef"'}

    updated = await service.confirm_upload(user_id, presigned["file_id"])

    assert updated.checksum_sha256 is None


async def test_confirm_upload_within_tolerance_succeeds(
    session: AsyncSession, redis: Redis, org, user_id, fake_backend
) -> None:
    service = _service(session, redis, org)
    presigned = await service.presign_upload(
        user_id, filename="f.pdf", content_type="application/pdf", size_bytes=1024, org_sub=None
    )
    await session.commit()
    fake_backend.uploaded[presigned["key"]] = {"ContentLength": 1024 + 1000, "ETag": '"abc"'}

    updated = await service.confirm_upload(user_id, presigned["file_id"])

    assert updated.status == "ready"


@pytest.mark.owasp("A08")
async def test_confirm_upload_rejects_content_sniff_mismatch_and_deletes_object(
    session: AsyncSession, redis: Redis, org, user_id, fake_backend
) -> None:
    # Declares application/pdf but uploads a PNG's actual bytes -- the
    # content-type allowlist check at presign time is declared-type-only and
    # can't catch this; confirm_upload's sniff step must.
    service = _service(session, redis, org)
    presigned = await service.presign_upload(
        user_id, filename="f.pdf", content_type="application/pdf", size_bytes=1024, org_sub=None
    )
    await session.commit()
    fake_backend.uploaded[presigned["key"]] = {"ContentLength": 1024, "ETag": '"abc"'}
    fake_backend.content[presigned["key"]] = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 64

    with pytest.raises(UnprocessableEntityError):
        await service.confirm_upload(user_id, presigned["file_id"])

    assert presigned["key"] in fake_backend.deleted


async def test_confirm_upload_skips_sniff_for_text_plain(
    session: AsyncSession, redis: Redis, org, user_id, fake_backend
) -> None:
    # text/plain has no reliable magic-byte signature -- any bytes must be
    # accepted rather than flagged as a "mismatch" against arbitrary content.
    service = _service(session, redis, org)
    presigned = await service.presign_upload(
        user_id, filename="f.txt", content_type="text/plain", size_bytes=1024, org_sub=None
    )
    await session.commit()
    fake_backend.uploaded[presigned["key"]] = {"ContentLength": 1024, "ETag": '"abc"'}
    fake_backend.content[presigned["key"]] = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 64

    updated = await service.confirm_upload(user_id, presigned["file_id"])

    assert updated.status == "ready"


async def test_confirm_upload_is_idempotent(
    session: AsyncSession, redis: Redis, org, user_id, fake_backend
) -> None:
    service = _service(session, redis, org)
    presigned = await service.presign_upload(
        user_id, filename="f.pdf", content_type="application/pdf", size_bytes=1024, org_sub=None
    )
    await session.commit()
    fake_backend.uploaded[presigned["key"]] = {"ContentLength": 1024, "ETag": '"abc"'}
    first = await service.confirm_upload(user_id, presigned["file_id"])

    second = await service.confirm_upload(user_id, presigned["file_id"])

    assert second.status == "ready"
    assert second.id == first.id


@pytest.mark.usefixtures("fake_backend")
async def test_confirm_upload_unknown_file_raises_not_found(
    session: AsyncSession, redis: Redis, org, user_id
) -> None:
    service = _service(session, redis, org)

    with pytest.raises(NotFoundError):
        await service.confirm_upload(user_id, uuid4())


# --- delete_file ----------------------------------------------------------------------------


async def test_delete_file_soft_deletes_and_enqueues_deferred_hard_delete(  # noqa: PLR0913 -- enqueued_jobs on top of the usual fixture set
    session: AsyncSession, redis: Redis, org, user_id, fake_backend, enqueued_jobs
) -> None:
    service = _service(session, redis, org)
    presigned = await service.presign_upload(
        user_id, filename="f.pdf", content_type="application/pdf", size_bytes=1024, org_sub=None
    )
    await session.commit()
    fake_backend.uploaded[presigned["key"]] = {"ContentLength": 1024, "ETag": '"abc"'}
    await service.confirm_upload(user_id, presigned["file_id"])

    await service.delete_file(user_id, presigned["file_id"])

    repo = StoredFileRepository(session, org.id, redis)
    with pytest.raises(NotFoundError):
        await repo.get_or_raise(presigned["file_id"])
    matching = [kwargs for fn, kwargs in enqueued_jobs if fn == "hard_delete_stored_file"]
    assert len(matching) == 1
    assert matching[0]["file_id"] == presigned["file_id"]
    assert matching[0]["_defer_by"] == timedelta(days=7)


async def test_delete_file_invalidates_total_bytes_cache(
    session: AsyncSession, redis: Redis, org, user_id, fake_backend
) -> None:
    service = _service(session, redis, org)
    presigned = await service.presign_upload(
        user_id, filename="f.pdf", content_type="application/pdf", size_bytes=1024, org_sub=None
    )
    await session.commit()
    fake_backend.uploaded[presigned["key"]] = {"ContentLength": 1024, "ETag": '"abc"'}
    await service.confirm_upload(user_id, presigned["file_id"])
    repo = StoredFileRepository(session, org.id, redis)
    assert await repo.total_bytes_for_org() == 1024

    await service.delete_file(user_id, presigned["file_id"])

    assert await repo.total_bytes_for_org() == 0


# --- get_download_url: the scan gate --------------------------------------------------------
#
# The finding this section exists to keep closed: get_download_url checked
# tenancy and then signed the object, with no reference to whether it had
# been scanned. confirm_upload sets "ready" and *then* enqueues the scan, so
# every confirmed file was signable during the window before a verdict
# existed -- and stayed signable if the scan came back "failed".


async def _confirmed_file(service: StorageService, user_id, fake_backend, session: AsyncSession):
    presigned = await service.presign_upload(
        user_id, filename="f.pdf", content_type="application/pdf", size_bytes=1024, org_sub=None
    )
    await session.commit()
    fake_backend.uploaded[presigned["key"]] = {"ContentLength": 1024, "ETag": '"x"'}
    return await service.confirm_upload(user_id, presigned["file_id"])


@pytest.mark.parametrize(
    ("status", "verdict", "expected_key"),
    [
        ("ready", None, "errors.file_scan_pending"),
        ("pending", None, "errors.file_not_ready"),
        ("failed", None, "errors.file_scan_failed"),
        ("infected", "Eicar-Test-Signature", "errors.file_infected"),
    ],
)
async def test_download_is_refused_until_a_clean_verdict_exists(  # noqa: PLR0913 -- parametrised over the fixture set
    session: AsyncSession,
    redis: Redis,
    org,
    user_id,
    fake_backend,
    status: str,
    verdict: str | None,
    expected_key: str,
) -> None:
    service = _service(session, redis, org)
    stored_file = await _confirmed_file(service, user_id, fake_backend, session)
    stored_file.status = status
    stored_file.virus_scan_result = verdict
    await session.commit()

    with pytest.raises(ConflictError) as exc_info:
        await service.get_download_url(stored_file.id)

    # Each state says something different on purpose: "try again shortly" and
    # "this file is gone" are not the same instruction to a user.
    assert exc_info.value.message_key == expected_key


async def test_download_succeeds_once_the_scan_reports_clean(
    session: AsyncSession, redis: Redis, org, user_id, fake_backend
) -> None:
    service = _service(session, redis, org)
    stored_file = await _confirmed_file(service, user_id, fake_backend, session)
    stored_file.virus_scan_result = "clean"
    await session.commit()

    url, expires_at = await service.get_download_url(stored_file.id)

    assert url.startswith("https://fake.example/download/")
    assert expires_at > datetime.now(UTC)


async def test_a_file_confirmed_but_not_yet_scanned_is_not_downloadable(
    session: AsyncSession, redis: Redis, org, user_id, fake_backend
) -> None:
    # The exact window the original bug lived in: confirm_upload has returned
    # "ready" and the virus_scan job has been enqueued but has not run.
    service = _service(session, redis, org)
    stored_file = await _confirmed_file(service, user_id, fake_backend, session)

    assert stored_file.status == "ready"
    assert stored_file.virus_scan_result is None
    with pytest.raises(ConflictError):
        await service.get_download_url(stored_file.id)


async def test_the_scan_gate_does_not_replace_the_tenancy_check(
    session: AsyncSession, redis: Redis, org, user_id, fake_backend
) -> None:
    # Ordering matters: a clean file belonging to another org must still be a
    # NotFoundError from the repository, not a ConflictError that confirms it
    # exists and is merely unscanned.
    service = _service(session, redis, org)
    stored_file = await _confirmed_file(service, user_id, fake_backend, session)
    stored_file.virus_scan_result = "clean"
    await session.commit()

    other_org_service = StorageService(session, uuid4(), redis)

    with pytest.raises(NotFoundError):
        await other_org_service.get_download_url(stored_file.id)
