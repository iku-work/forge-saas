"""
StorageBackend protocol (T-111): a single interface over S3, Cloudflare R2,
and MinIO, selected at runtime by settings.STORAGE_BACKEND.

Deliberately uses boto3 (sync) off-loaded via asyncio.to_thread in each
backends/*.py implementation, not aioboto3 -- matching the precedent already
set by app.workers.jobs.gdpr._s3_client/_upload_export_zip (T-037), which
chose the same approach for the same reason (this project already pulls in
boto3 as its one S3 client dependency; aioboto3 would be a second one
wrapping the first just for this one module). Once this module exists, a
future task could migrate that GDPR export job onto it instead of talking to
boto3 directly -- not done here, out of T-111's own Build section (see
gdpr.py's own module docstring, which already names T-111 as the trigger for
that follow-up).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Protocol

from app.core.config import get_settings

if TYPE_CHECKING:
    from datetime import datetime


class ObjectChangedError(Exception):
    """A conditional copy's source was not the object the caller checked.

    Raised by `copy_if_match` when the source's ETag no longer matches (it
    was overwritten in between) or the source is gone. Not transient: trying
    again copies whatever is there *now*, which is exactly what the
    condition exists to prevent. So app.core.resilience neither retries it
    nor counts it against the "s3" breaker.
    """


@dataclass(frozen=True)
class PresignedUpload:
    url: str
    fields: dict[str, str]  # POST form fields for the upload
    key: str
    expires_at: datetime


class StorageBackend(Protocol):
    async def presign_upload(
        self, key: str, content_type: str, max_bytes: int, expires_in: int = 600
    ) -> PresignedUpload: ...

    async def presign_download(
        self, key: str, expires_in: int = 3600, *, filename: str | None = None
    ) -> str: ...

    async def head(self, key: str) -> dict[str, Any] | None: ...  # None if not found

    async def read_prefix(self, key: str, num_bytes: int) -> bytes: ...

    async def copy_if_match(self, source_key: str, destination_key: str, etag: str) -> None:
        """Server-side copy of `source_key` to `destination_key`, only if the
        source's ETag is still `etag`. Raises ObjectChangedError otherwise."""
        ...

    async def delete(self, key: str) -> None: ...

    async def exists(self, key: str) -> bool: ...


@lru_cache
def get_storage_backend() -> StorageBackend:
    from app.modules.storage.backends.s3 import S3ClientOptions  # noqa: PLC0415

    settings = get_settings()
    options = S3ClientOptions(
        access_key_id=settings.AWS_ACCESS_KEY_ID,
        secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        endpoint_url=settings.AWS_ENDPOINT_URL,
    )

    if settings.STORAGE_BACKEND == "s3":
        from app.modules.storage.backends.s3 import S3Backend  # noqa: PLC0415

        return S3Backend(
            bucket=settings.AWS_BUCKET_NAME, region=settings.AWS_REGION, options=options
        )

    if settings.STORAGE_BACKEND == "r2":
        from app.modules.storage.backends.r2 import R2Backend  # noqa: PLC0415

        return R2Backend(
            bucket=settings.AWS_BUCKET_NAME, region=settings.AWS_REGION, options=options
        )

    from app.modules.storage.backends.minio import MinioBackend  # noqa: PLC0415

    return MinioBackend(
        bucket=settings.AWS_BUCKET_NAME, region=settings.AWS_REGION, options=options
    )
