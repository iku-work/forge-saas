"""
S3Backend (T-111): boto3-backed StorageBackend implementation, talking to
real AWS S3 by default. R2Backend/MinioBackend (sibling modules in this
package) are thin subclasses that point this same client at a different
endpoint/addressing-style/signature-version -- S3, R2, and MinIO are all
S3-API-compatible, so one boto3 implementation covers every backend; only
the constructor arguments selecting those three knobs differ per provider.

Every synchronous boto3 call is off-loaded to a thread via asyncio.to_thread
so it doesn't block the event loop -- see backend.py's own module docstring
for why this project uses boto3 (sync) rather than aioboto3.

T-206: `presign_upload`/`presign_download`/`head`/`delete` are each wrapped in
`@resilient("s3")` -- one breaker failure per real network round-trip.
`exists()` deliberately isn't decorated itself: it just calls `head()`, which
is already decorated, and double-wrapping would count one logical call
twice against the shared "s3" breaker.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from app.core.resilience import resilient
from app.modules.storage.backend import PresignedUpload

_SSE_ALGORITHM = "AES256"
_NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound"}


@dataclass(frozen=True)
class S3ClientOptions:
    """boto3 client knobs that vary by provider (S3 vs R2 vs MinIO) -- bundled
    into one object so S3Backend.__init__ (and its R2Backend/MinioBackend
    subclasses) don't trip ruff's PLR0913 (too-many-arguments)."""

    access_key_id: str = ""
    secret_access_key: str = ""
    endpoint_url: str | None = None
    addressing_style: str | None = None
    signature_version: str | None = None


class S3Backend:
    def __init__(self, *, bucket: str, region: str, options: S3ClientOptions | None = None) -> None:
        options = options or S3ClientOptions()
        self._bucket = bucket
        config_kwargs: dict[str, Any] = {}
        if options.signature_version:
            config_kwargs["signature_version"] = options.signature_version
        if options.addressing_style:
            config_kwargs["s3"] = {"addressing_style": options.addressing_style}
        self._client = boto3.client(
            "s3",
            region_name=region,
            endpoint_url=options.endpoint_url,
            aws_access_key_id=options.access_key_id or None,
            aws_secret_access_key=options.secret_access_key or None,
            config=BotoConfig(**config_kwargs) if config_kwargs else None,
        )

    @resilient("s3")
    async def presign_upload(
        self, key: str, content_type: str, max_bytes: int, expires_in: int = 600
    ) -> PresignedUpload:
        def _presign() -> dict[str, Any]:
            fields = {
                "Content-Type": content_type,
                "x-amz-server-side-encryption": _SSE_ALGORITHM,
            }
            conditions: list[Any] = [
                ["content-length-range", 0, max_bytes],
                {"Content-Type": content_type},
                {"x-amz-server-side-encryption": _SSE_ALGORITHM},
            ]
            response: dict[str, Any] = self._client.generate_presigned_post(
                Bucket=self._bucket,
                Key=key,
                Fields=fields,
                Conditions=conditions,
                ExpiresIn=expires_in,
            )
            return response

        response = await asyncio.to_thread(_presign)
        return PresignedUpload(
            url=response["url"],
            fields=response["fields"],
            key=key,
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
        )

    @resilient("s3")
    async def presign_download(
        self, key: str, expires_in: int = 3600, *, filename: str | None = None
    ) -> str:
        def _presign() -> str:
            params: dict[str, str] = {"Bucket": self._bucket, "Key": key}
            if filename:
                params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
            url: str = self._client.generate_presigned_url(
                "get_object", Params=params, ExpiresIn=expires_in
            )
            return url

        return await asyncio.to_thread(_presign)

    @resilient("s3")
    async def head(self, key: str) -> dict[str, Any] | None:
        def _head() -> dict[str, Any] | None:
            try:
                response: dict[str, Any] = self._client.head_object(Bucket=self._bucket, Key=key)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if code in _NOT_FOUND_CODES:
                    return None
                raise
            return response

        return await asyncio.to_thread(_head)

    @resilient("s3")
    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=key)

    async def exists(self, key: str) -> bool:
        return await self.head(key) is not None
