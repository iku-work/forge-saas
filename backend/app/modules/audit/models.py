"""
AuditEvent (T-028): append-only audit log written on every mutating action
in auth and tenancy. §0.3 Immutability: written once, never UPDATE or DELETE
— there is deliberately no repository method for either.

Doesn't use TimestampMixin/SoftDeleteMixin/OrgScopedMixin like other tables:
no updated_at or deleted_at (nothing here is ever modified or removed), and
org_id isn't a foreign_key so the row survives organization deletion intact.
"""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models._mixins import utcnow

_TZ_DATETIME = DateTime(timezone=True)


class AuditEvent(SQLModel, table=True):
    __tablename__ = "audit_event"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    ts: datetime = Field(default_factory=utcnow, index=True, sa_type=_TZ_DATETIME)  # type: ignore[call-overload]
    # Nullable: actions with no org context yet (registration, login) have no
    # org to attribute the event to. Org-scoped actions (role change, invite)
    # always set it.
    org_id: UUID | None = Field(default=None, index=True)
    actor_id: UUID | None = Field(default=None)  # null for system events
    action: str  # "user.registered", "membership.role_changed"
    resource_type: str  # "user", "membership"
    resource_id: str | None = Field(default=None)
    diff: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    ip_address: str | None = Field(default=None)
    user_agent: str | None = Field(default=None)
    # T-141: lets admin-audit alerting query "everything elevated" without
    # parsing `action` strings. Defaults to "info" so every pre-existing
    # caller (auth, tenancy, T-122's feature-flag overrides) needs no change;
    # only T-141's admin write endpoints pass severity="elevated".
    severity: str = Field(default="info", index=True)
