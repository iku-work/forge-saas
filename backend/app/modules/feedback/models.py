"""Feedback a signed-in user sends from the app, read by superadmins in the
admin Feedback inbox.

A message belongs to the person who sent it (§0.2's user-scoped list), like a
notification. `org_id` is the org they were working in when they sent it, if
any, so the inbox can say where a problem happened; it is checked against
their memberships when the message is saved.
"""

from datetime import datetime
from typing import ClassVar
from uuid import UUID

from sqlalchemy import DateTime, Index
from sqlmodel import Field, SQLModel

from app.models._mixins import SoftDeleteMixin, TimestampMixin, UUIDPKMixin

_TZ_DATETIME = DateTime(timezone=True)


class Feedback(UUIDPKMixin, TimestampMixin, SoftDeleteMixin, SQLModel, table=True):
    __tablename__ = "feedback"
    __table_args__: ClassVar[tuple[Index, ...]] = (
        # The inbox: open (or handled) messages, newest first, by cursor.
        Index("ix_feedback_resolved_at_created_at_id", "resolved_at", "created_at", "id"),
    )

    user_id: UUID = Field(foreign_key="user.id", index=True)
    org_id: UUID | None = Field(default=None, foreign_key="organization.id", index=True)
    category: str  # "bug" | "idea" | "other"
    message: str
    # The app path the form was opened on, without its query string.
    page: str | None = Field(default=None)
    resolved_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)
    # Not a foreign key, like AuditEvent.actor_id: the row outlives the admin.
    resolved_by: UUID | None = Field(default=None)
