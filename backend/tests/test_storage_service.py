"""
Verifies T-112's StorageService: content-type allowlist enforcement
(per-bucket overrides included), the two plan-quota checks (per-file
max_upload_mb cap, org-wide storage_gb cap), confirm_upload's three
orchestration-layer security requirements (existence via head(), size
tolerance, idempotent re-confirm), checksum extraction from ETag, the
virus_scan job enqueue, and delete_file's soft-delete + deferred hard-delete
enqueue.

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

from app.core.exceptions import NotFoundError, PlanLimitError, UnprocessableEntityError
from app.modules.auth.repository import UserRepository
from app.modules.billing.dependencies import OrgSubscription
from app.modules.billing.models import Plan
from app.modules.storage import service as service_module
from app.modules.storage.backend import PresignedUpload
from app.modules.storage.repository import StoredFileRepository
from app.modules.storage.service import StorageService
from app.modules.tenancy.repository import OrganizationRepository


@dataclass
class _FakeStorageBackend:
    uploaded: dict[str, dict[str, Any]] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)

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


async def test_confirm_upload_happy_path_sets_ready_and_checksum_and_enqueues_scan(  # noqa: PLR0913 -- enqueued_jobs on top of the usual fixture set
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
    assert updated.checksum_sha256 == "deadbeef"
    assert ("virus_scan", {"file_id": updated.id}) in enqueued_jobs


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
