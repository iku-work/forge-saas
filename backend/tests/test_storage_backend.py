"""
T-111: StorageBackend protocol + S3/R2/MinIO implementations.

All three implementations are S3-API-compatible, so a single real MinIO
testcontainer backs every parametrized `backend` test case -- S3Backend and
R2Backend are pointed at the same MinIO endpoint with path-style addressing
forced via S3ClientOptions, exactly the way the production classes would be
pointed at real AWS S3 / Cloudflare R2 (only the endpoint/addressing differ,
never the protocol behaviour being exercised here).
"""

import base64
import secrets
from collections.abc import Generator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from botocore.exceptions import ClientError
from freezegun import freeze_time
from testcontainers.minio import MinioContainer

from app.core.config import get_settings
from app.modules.storage.backend import PresignedUpload, get_storage_backend
from app.modules.storage.backends.minio import MinioBackend
from app.modules.storage.backends.r2 import R2Backend
from app.modules.storage.backends.s3 import S3Backend, S3ClientOptions

_BUCKET = "storage-backend-tests"

# The testcontainers-pinned default MinIO image (RELEASE.2022-12-02) predates
# MinIO's built-in single-key KMS and returns 501 Not Implemented for any
# upload carrying x-amz-server-side-encryption: AES256 -- the exact header
# T-111's own security requirements mandate be signed into every presigned
# upload. A recent image + MINIO_KMS_SECRET_KEY (MinIO's local, no-Vault-
# required auto-encryption key) is required for SSE-S3 to actually work, not
# just for the policy condition to be present.
_MINIO_IMAGE = "minio/minio:RELEASE.2025-09-07T16-13-09Z"


@pytest.fixture(scope="session")
def minio_container() -> Generator[MinioContainer, None, None]:
    kms_key = base64.b64encode(secrets.token_bytes(32)).decode()
    with MinioContainer(_MINIO_IMAGE).with_env(
        "MINIO_KMS_SECRET_KEY", f"forge-test-key:{kms_key}"
    ) as container:
        yield container


@pytest.fixture(scope="session", autouse=True)
def _minio_bucket(minio_container: MinioContainer) -> None:
    client = minio_container.get_client()
    if not client.bucket_exists(_BUCKET):
        client.make_bucket(_BUCKET)


def _options(minio_container: MinioContainer) -> S3ClientOptions:
    config = minio_container.get_config()
    return S3ClientOptions(
        access_key_id=config["access_key"],
        secret_access_key=config["secret_key"],
        endpoint_url=f"http://{config['endpoint']}",
        addressing_style="path",
    )


@pytest.fixture(params=["s3", "r2", "minio"])
def backend(request: pytest.FixtureRequest, minio_container: MinioContainer) -> S3Backend:
    options = _options(minio_container)
    if request.param == "s3":
        return S3Backend(bucket=_BUCKET, region="us-east-1", options=options)
    if request.param == "r2":
        return R2Backend(bucket=_BUCKET, region="us-east-1", options=options)
    return MinioBackend(bucket=_BUCKET, region="us-east-1", options=options)


@pytest.fixture(autouse=True)
def _clear_storage_backend_cache() -> Generator[None]:
    get_storage_backend.cache_clear()
    yield
    get_storage_backend.cache_clear()


def _post_upload(
    presigned: PresignedUpload,
    content: bytes,
    *,
    content_type: str,
    key: str | None = None,
    drop_field: str | None = None,
) -> httpx.Response:
    fields = dict(presigned.fields)
    # The "Content-Type" *form field* (not the multipart part's own MIME
    # header) is what S3-compatible POST policy conditions actually check --
    # always set it explicitly so callers can tamper with it.
    fields["Content-Type"] = content_type
    if key is not None:
        fields["key"] = key
    if drop_field is not None:
        fields.pop(drop_field, None)
    files = {"file": ("upload.bin", content, content_type)}
    return httpx.post(presigned.url, data=fields, files=files)


def _get(url: str) -> httpx.Response:
    return httpx.get(url)


# --- round trip ---


async def test_round_trip_presign_upload_then_head(backend: S3Backend) -> None:
    key = f"round-trip/{uuid4()}.jpg"
    presigned = await backend.presign_upload(key, "image/jpeg", max_bytes=1024)

    response = _post_upload(presigned, b"hello world", content_type="image/jpeg")

    assert response.status_code in (200, 201, 204)
    metadata = await backend.head(key)
    assert metadata is not None
    assert metadata["ContentLength"] == len(b"hello world")


async def test_delete_removes_object_then_exists_is_false(backend: S3Backend) -> None:
    key = f"delete/{uuid4()}.jpg"
    presigned = await backend.presign_upload(key, "image/jpeg", max_bytes=1024)
    _post_upload(presigned, b"data", content_type="image/jpeg")
    assert await backend.exists(key) is True

    await backend.delete(key)

    assert await backend.exists(key) is False


async def test_presign_download_round_trips_content(backend: S3Backend) -> None:
    key = f"download/{uuid4()}.jpg"
    presigned = await backend.presign_upload(key, "image/jpeg", max_bytes=1024)
    _post_upload(presigned, b"download me", content_type="image/jpeg")

    url = await backend.presign_download(key)
    response = _get(url)

    assert response.status_code == 200
    assert response.content == b"download me"


async def test_presign_download_with_filename_sets_content_disposition(backend: S3Backend) -> None:
    key = f"download-filename/{uuid4()}.jpg"
    presigned = await backend.presign_upload(key, "image/jpeg", max_bytes=1024)
    _post_upload(presigned, b"content", content_type="image/jpeg")

    url = await backend.presign_download(key, filename="report.jpg")
    response = _get(url)

    assert response.status_code == 200
    assert "report.jpg" in response.headers.get("content-disposition", "")


async def test_head_returns_none_when_missing(backend: S3Backend) -> None:
    assert await backend.head(f"missing/{uuid4()}.jpg") is None


async def test_exists_false_when_missing(backend: S3Backend) -> None:
    assert await backend.exists(f"missing/{uuid4()}.jpg") is False


async def test_head_reraises_non_not_found_client_errors(minio_container: MinioContainer) -> None:
    # Wrong credentials -> SignatureDoesNotMatch (403), not a "not found" code
    # -- head() must propagate this rather than swallowing it as a 404 would.
    options = replace(_options(minio_container), secret_access_key="wrong-secret")
    backend = MinioBackend(bucket=_BUCKET, region="us-east-1", options=options)

    with pytest.raises(ClientError):
        await backend.head("whatever.jpg")


def test_s3_backend_defaults_with_no_options_override() -> None:
    backend = S3Backend(bucket="some-bucket", region="us-east-1")

    assert backend._client.meta.region_name == "us-east-1"


async def test_presign_upload_defaults_to_600_second_expiry(backend: S3Backend) -> None:
    before = datetime.now(UTC)
    presigned = await backend.presign_upload(f"ttl/{uuid4()}.jpg", "image/jpeg", max_bytes=1024)
    after = datetime.now(UTC)

    assert before + timedelta(seconds=600) <= presigned.expires_at <= after + timedelta(seconds=600)


# --- the 5 non-negotiable presigned URL security requirements ---


async def test_content_length_range_is_signed_oversized_put_rejected(backend: S3Backend) -> None:
    key = f"security/oversized/{uuid4()}.jpg"
    presigned = await backend.presign_upload(key, "image/jpeg", max_bytes=1024)

    response = _post_upload(presigned, b"x" * 2048, content_type="image/jpeg")

    assert response.status_code == 400
    assert await backend.exists(key) is False


async def test_content_type_is_signed_as_exact_match(backend: S3Backend) -> None:
    key = f"security/content-type/{uuid4()}.jpg"
    presigned = await backend.presign_upload(key, "image/jpeg", max_bytes=1024)

    response = _post_upload(presigned, b"data", content_type="application/octet-stream")

    # Verified empirically against real MinIO: every POST-policy condition
    # mismatch other than content-length-range comes back as 403 AccessDenied
    # ("Invalid according to Policy: Policy Condition failed"), not 400 --
    # content-length-range is the one condition MinIO/S3 special-case to 400
    # EntityTooLarge. All five checks below assert the real observed code.
    assert response.status_code == 403
    assert await backend.exists(key) is False


async def test_server_side_encryption_header_is_required(backend: S3Backend) -> None:
    key = f"security/sse/{uuid4()}.jpg"
    presigned = await backend.presign_upload(key, "image/jpeg", max_bytes=1024)

    response = _post_upload(
        presigned,
        b"data",
        content_type="image/jpeg",
        drop_field="x-amz-server-side-encryption",
    )

    assert response.status_code == 403
    assert await backend.exists(key) is False


async def test_exact_object_key_is_bound_to_the_signature(backend: S3Backend) -> None:
    key_a = f"org-a/files/{uuid4()}/x.jpg"
    key_b = f"org-b/files/{uuid4()}/y.jpg"
    presigned = await backend.presign_upload(key_a, "image/jpeg", max_bytes=1024)

    response = _post_upload(presigned, b"data", content_type="image/jpeg", key=key_b)

    assert response.status_code == 403
    assert await backend.exists(key_a) is False
    assert await backend.exists(key_b) is False


async def test_expired_presigned_upload_is_rejected(backend: S3Backend) -> None:
    key = f"security/expiry/{uuid4()}.jpg"
    eleven_minutes_ago = datetime.now(UTC) - timedelta(minutes=11)
    with freeze_time(eleven_minutes_ago):
        # default expires_in=600s (10 min), signed as if it were generated
        # 11 minutes ago -- already expired by the time this request lands.
        presigned = await backend.presign_upload(key, "image/jpeg", max_bytes=1024)

    response = _post_upload(presigned, b"data", content_type="image/jpeg")

    assert response.status_code == 403
    assert await backend.exists(key) is False


# --- get_storage_backend() factory ---


def test_get_storage_backend_selects_s3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    get_settings.cache_clear()

    backend = get_storage_backend()

    assert type(backend).__name__ == "S3Backend"


def test_get_storage_backend_selects_r2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "r2")
    get_settings.cache_clear()

    backend = get_storage_backend()

    assert type(backend).__name__ == "R2Backend"


def test_get_storage_backend_selects_minio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "minio")
    get_settings.cache_clear()

    backend = get_storage_backend()

    assert type(backend).__name__ == "MinioBackend"


def test_get_storage_backend_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "minio")
    get_settings.cache_clear()

    assert get_storage_backend() is get_storage_backend()
