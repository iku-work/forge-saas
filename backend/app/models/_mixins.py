from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


# The mixins subclass SQLModel rather than being plain classes so the type
# checker sees their fields in every model's constructor; as plain classes,
# `Notification(org_id=...)` was an unexpected keyword under sqlmodel 0.0.47.
class UUIDPKMixin(SQLModel):
    id: UUID = Field(default_factory=uuid4, primary_key=True)


class TimestampMixin(SQLModel):
    # sa_type=DateTime(timezone=True): columns must be TIMESTAMPTZ, not the
    # SQLAlchemy-default TIMESTAMP WITHOUT TIME ZONE — every value here is
    # tz-aware UTC (utcnow()), and asyncpg rejects binding aware datetimes
    # against a naive column.
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),
    )
    updated_at: datetime = Field(
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"onupdate": utcnow},
    )


class SoftDeleteMixin(SQLModel):
    deleted_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),
    )


class OrgScopedMixin(SQLModel):
    org_id: UUID = Field(foreign_key="organization.id", index=True)
