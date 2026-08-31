from datetime import datetime
from typing import ClassVar
from uuid import UUID

from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models._mixins import SoftDeleteMixin, TimestampMixin, UUIDPKMixin
from app.modules.auth.permissions import MemberRole

# Same convention as app.modules.auth.models: any datetime column declared
# directly here (not inherited from TimestampMixin) must stay tz-aware so
# asyncpg doesn't reject the bind.
_TZ_DATETIME = DateTime(timezone=True)


def member_role_column() -> SAEnum:
    """A `MemberRole` column, as this schema always spells one.

    native_enum=False renders it as VARCHAR + CHECK instead of a standalone
    Postgres CREATE TYPE. Three tables use it now (Membership, Invitation,
    and T-331's SsoConnection); a native PG enum type is a single DB-wide
    object that op.drop_table() never cleans up on downgrade, so a later
    upgrade fails on "type already exists". A CHECK constraint is per-column,
    so it round-trips with the table. A fresh instance per call, rather than
    one shared object, avoids coupling the tables' columns together.

    Public -- it lost its underscore in T-331 -- because `sso_connection` is
    on a different module's migration branch and has to spell the column the
    same way, and two copies of this comment would be one too many.
    """
    return SAEnum(MemberRole, name="memberrole", native_enum=False, length=20)


class Organization(UUIDPKMixin, TimestampMixin, SoftDeleteMixin, SQLModel, table=True):
    __tablename__ = "organization"

    name: str
    slug: str = Field(unique=True, index=True)
    logo_url: str | None = Field(default=None)
    settings: dict[str, object] = Field(sa_column=Column(JSONB, nullable=False, default=dict))
    # Intentionally unused/null: entitlement is resolved from the org's
    # Subscription row, not this column (see billing.dependencies' docstring).
    # Stays nullable so non-billing products (T-065) never need to populate
    # it, and the billing module's tables can be absent entirely.
    plan_id: UUID | None = Field(default=None, foreign_key="plan.id", index=True)
    stripe_customer_id: str | None = Field(default=None)


class Membership(UUIDPKMixin, TimestampMixin, SQLModel, table=True):
    __tablename__ = "membership"
    __table_args__: ClassVar[tuple[UniqueConstraint]] = (UniqueConstraint("user_id", "org_id"),)

    user_id: UUID = Field(foreign_key="user.id", index=True)
    org_id: UUID = Field(foreign_key="organization.id", index=True)
    role: MemberRole = Field(sa_column=Column(member_role_column(), nullable=False))
    invited_by: UUID | None = Field(default=None, foreign_key="user.id")


class Invitation(UUIDPKMixin, TimestampMixin, SQLModel, table=True):
    __tablename__ = "invitation"

    org_id: UUID = Field(foreign_key="organization.id", index=True)
    email: str = Field(index=True)
    role: MemberRole = Field(sa_column=Column(member_role_column(), nullable=False))
    token_hash: str = Field(unique=True)  # SHA-256 hex — never the plain token
    invited_by: UUID = Field(foreign_key="user.id")
    expires_at: datetime = Field(sa_type=_TZ_DATETIME)  # type: ignore[call-overload]
    accepted_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)  # type: ignore[call-overload]


class OrgFeatureFlag(UUIDPKMixin, TimestampMixin, SQLModel, table=True):
    __tablename__ = "org_feature_flag"
    __table_args__: ClassVar[tuple[UniqueConstraint]] = (UniqueConstraint("org_id", "flag_name"),)

    org_id: UUID = Field(foreign_key="organization.id", index=True)
    flag_name: str
    enabled: bool = Field(default=True)
