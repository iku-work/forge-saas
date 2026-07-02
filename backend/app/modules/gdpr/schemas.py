"""
Request/response schemas for the GDPR module (T-033). Always distinct from
the SQLModel table classes in app.modules.gdpr.models — a router must never
accept or return a model directly (§0.4's mass-assignment rule).
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field

from app.core.schemas import AppBaseModel


class ExportJobCreated(AppBaseModel):
    job_id: UUID


class DataExportJobPublic(AppBaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: str
    download_url: str | None
    expires_at: datetime | None
    created_at: datetime


class DeleteMeRequest(AppBaseModel):
    confirm: Literal["DELETE"]


class ConsentRecordPublic(AppBaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    consent_type: str
    granted: bool
    version: str
    ts: datetime


class RecordConsentRequest(AppBaseModel):
    consent_type: str = Field(min_length=1, max_length=50)
    granted: bool
    version: str = Field(min_length=1, max_length=50)
