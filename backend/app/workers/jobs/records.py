"""
JobRecord (T-034): a queryable trail of ARQ job executions, separate from
ARQ's own Redis-only result storage (which expires and isn't queryable by
org or job_type). Not org-scoped via OrgScopedMixin: org_id is nullable
because system jobs (e.g. reencrypt_column_job) have no org, mirroring
AuditEvent's org_id (app.modules.audit.models) — present for filtering,
but not a FK so a row survives org deletion.
"""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models._mixins import utcnow

_TZ_DATETIME = DateTime(timezone=True)


class JobRecord(SQLModel, table=True):
    __tablename__ = "job_record"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: UUID | None = Field(default=None, index=True)
    job_type: str = Field(index=True)
    status: str = Field(default="queued")  # "queued" | "running" | "done" | "failed"
    arq_job_id: str | None = Field(default=None, index=True)
    payload: dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))
    result: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    error: str | None = Field(default=None)
    attempts: int = Field(default=0)
    created_at: datetime = Field(default_factory=utcnow, sa_type=_TZ_DATETIME)  # type: ignore[call-overload]
    started_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)  # type: ignore[call-overload]
    completed_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)  # type: ignore[call-overload]
