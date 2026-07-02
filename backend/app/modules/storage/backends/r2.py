"""
R2Backend (T-111): Cloudflare R2 is S3-API-compatible, so this just points
S3Backend's boto3 client at R2's account-specific endpoint with
signature_version="s3v4" forced -- R2 requires SigV4 explicitly rather than
letting botocore negotiate a default. Every other client option (including
`endpoint_url`, which callers must set to R2's account endpoint) passes
through `options` unchanged -- this also lets the test suite reuse the same
options it builds for MinioBackend, with path-style addressing, to point an
R2Backend instance at a local MinIO container; see
tests/test_storage_backend.py.
"""

from __future__ import annotations

from dataclasses import replace

from app.modules.storage.backends.s3 import S3Backend, S3ClientOptions


class R2Backend(S3Backend):
    def __init__(self, *, bucket: str, region: str, options: S3ClientOptions) -> None:
        super().__init__(
            bucket=bucket,
            region=region,
            options=replace(options, signature_version="s3v4"),
        )
