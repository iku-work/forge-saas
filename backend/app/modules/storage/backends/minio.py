"""
MinioBackend (T-111): MinIO is S3-API-compatible, so this just points
S3Backend's boto3 client at the configured endpoint with path-style
addressing forced -- a local/self-hosted MinIO has no wildcard DNS for
virtual-hosted-style bucket subdomains, unlike real AWS S3. Every other
client option (including `endpoint_url`, which callers must set to the
MinIO server's URL) passes through `options` unchanged.
"""

from __future__ import annotations

from dataclasses import replace

from app.modules.storage.backends.s3 import S3Backend, S3ClientOptions


class MinioBackend(S3Backend):
    def __init__(self, *, bucket: str, region: str, options: S3ClientOptions) -> None:
        super().__init__(
            bucket=bucket,
            region=region,
            options=replace(options, addressing_style="path"),
        )
