"""Request/response schemas for the storage module (T-112)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field

from app.core.schemas import AppBaseModel


class PresignUploadRequest(AppBaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)
    bucket: str = "default"


class PresignUploadResponse(AppBaseModel):
    file_id: UUID
    upload_url: str
    fields: dict[str, str]
    key: str
    expires_at: datetime


class FileUploaderSummary(AppBaseModel):
    """Who uploaded a file, for the Files list's uploader column."""

    full_name: str | None
    email: str


class StoredFilePublic(AppBaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    filename: str
    content_type: str
    size_bytes: int
    # The closed set the service actually writes, not a bare `str`. It used
    # to be the latter, which made the generated client type it `string` --
    # so no frontend could exhaustively handle it, and a page deciding what
    # to render per status had no compiler help at all (repo finding 12).
    # Widening information, not a breaking change: every one of these is
    # still assignable to the `string` a consumer may have stored it in.
    status: Literal["pending", "ready", "infected", "failed", "deleted"]
    # "ready" doesn't change when the scan comes back clean (only "infected"/
    # "failed" change status -- see workers.jobs.virus_scan) -- this is the
    # only observable signal that a scan has actually completed vs. is still
    # pending on a file that already reads "ready".
    virus_scan_result: str | None
    bucket: str
    uploaded_by: UUID
    created_at: datetime
    # When the row last changed; for a file still awaiting a verdict that is
    # its confirmation, which is when the scan starts. `created_at` is the
    # presign, which can be most of the upload window earlier (review OCT-06).
    updated_at: datetime
    # Null when the uploader's account is gone; `uploaded_by` still says who.
    uploader: FileUploaderSummary | None = None


class DownloadUrlResponse(AppBaseModel):
    url: str
    expires_at: datetime
