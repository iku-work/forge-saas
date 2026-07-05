"""Request/response schemas for the storage module (T-112)."""

from datetime import datetime
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


class StoredFilePublic(AppBaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    filename: str
    content_type: str
    size_bytes: int
    status: str
    # "ready" doesn't change when the scan comes back clean (only "infected"/
    # "failed" change status -- see workers.jobs.virus_scan) -- this is the
    # only observable signal that a scan has actually completed vs. is still
    # pending on a file that already reads "ready".
    virus_scan_result: str | None
    bucket: str
    uploaded_by: UUID
    created_at: datetime


class DownloadUrlResponse(AppBaseModel):
    url: str
    expires_at: datetime
