"""
GDPR models (T-032): ConsentRecord and DataExportJob.

Both are user-scoped, not org-scoped — per §0.2 they belong alongside
User/Session/OAuthAccount/TOTPSecret as one of the named exceptions to the
org_id rule, since consent and exports are personal, not organizational.

ConsentRecord doesn't use TimestampMixin/SoftDeleteMixin: it's append-only
(§0.3 immutability — granting or revoking consent is a new row, never an
update to an old one) and uses a single `ts` field instead of created_at, so
there is deliberately no repository method that updates or deletes a row
once written (see repository.py).
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime
from sqlmodel import Field, SQLModel

from app.models._mixins import TimestampMixin, UUIDPKMixin, utcnow

_TZ_DATETIME = DateTime(timezone=True)


class ConsentRecord(SQLModel, table=True):
    __tablename__ = "consent_record"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.id", index=True)
    consent_type: str  # "marketing" | "analytics" | "cookies"
    granted: bool
    version: str  # policy version accepted
    ip_address: str
    user_agent: str
    ts: datetime = Field(default_factory=utcnow, sa_type=_TZ_DATETIME)


class DataExportJob(UUIDPKMixin, TimestampMixin, SQLModel, table=True):
    __tablename__ = "data_export_job"

    user_id: UUID = Field(foreign_key="user.id", index=True)
    status: str = Field(default="pending")  # "pending" | "ready" | "failed" | "expired"
    download_url: str | None = Field(default=None)
    expires_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)
