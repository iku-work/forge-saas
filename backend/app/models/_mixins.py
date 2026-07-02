from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime
from sqlmodel import Field


def utcnow() -> datetime:
    return datetime.now(UTC)


class UUIDPKMixin:
    id: UUID = Field(default_factory=uuid4, primary_key=True)


class TimestampMixin:
    # sa_type=DateTime(timezone=True): columns must be TIMESTAMPTZ, not the
    # SQLAlchemy-default TIMESTAMP WITHOUT TIME ZONE — every value here is
    # tz-aware UTC (utcnow()), and asyncpg rejects binding aware datetimes
    # against a naive column. SQLModel's Field() stub types sa_type as
    # `type[Any]` only, but sqlmodel passes it straight through to
    # `Column(sa_type, ...)`, which accepts a configured instance too.
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),  # type: ignore[call-overload]
    )
    updated_at: datetime = Field(
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),  # type: ignore[call-overload]
        sa_column_kwargs={"onupdate": utcnow},
    )


class SoftDeleteMixin:
    deleted_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore[call-overload]
    )


class OrgScopedMixin:
    org_id: UUID = Field(foreign_key="organization.id", index=True)
