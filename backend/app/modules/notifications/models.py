"""T-101: Notification + PushToken tables.

Notification deliberately skips SoftDeleteMixin (unlike most user-data
tables, see §0.3) -- notifications are pruned by a hard-delete retention job,
never soft-deleted, so there's no value in a deleted_at column every read
query would otherwise have to filter on.
"""

from datetime import datetime
from typing import ClassVar
from uuid import UUID

from sqlalchemy import Column, DateTime, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models._mixins import OrgScopedMixin, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

# Same convention as the other modules' models.py: any datetime column
# declared directly here (not inherited from a mixin) must stay tz-aware so
# asyncpg doesn't reject the bind.
_TZ_DATETIME = DateTime(timezone=True)


class Notification(UUIDPKMixin, TimestampMixin, OrgScopedMixin, SQLModel, table=True):
    __tablename__ = "notification"
    __table_args__: ClassVar[tuple[Index, ...]] = (
        # Hot path (T-102): unread-count lookup for a single user.
        Index("ix_notification_user_id_read_at", "user_id", "read_at"),
        # Cursor pagination, mirrors BaseRepository.list_cursor's
        # ORDER BY created_at DESC, id DESC -- an ascending btree index serves
        # a descending scan equally well via Postgres' backward index scan, so
        # there's no need for a DESC-specific index definition here.
        Index(
            "ix_notification_org_id_user_id_created_at_id",
            "org_id",
            "user_id",
            "created_at",
            "id",
        ),
        # T-102: NotificationService.send()'s idempotency check --
        # "same org+user+key within the last 24h" -- filters on all three.
        Index(
            "ix_notification_org_id_user_id_idempotency_key",
            "org_id",
            "user_id",
            "idempotency_key",
        ),
    )

    user_id: UUID = Field(foreign_key="user.id", index=True)
    type: str = Field(index=True)  # "member_invited" | "payment_failed" | "weekly_summary" | ...
    title: str
    body: str
    action_url: str | None = Field(default=None)
    # e.g. ["email", "in_app", "push"] -- resolved from user preferences by
    # NotificationService.send() (T-102), not chosen by the caller.
    channels: list[str] = Field(sa_column=Column(JSONB, nullable=False))
    # T-102: caller-supplied dedup key (e.g. a Stripe event id) so a webhook
    # retry can't fan out the same notification twice. Unlike WebhookEvent's
    # id (a hard PK), this is just an indexed lookup the service checks
    # before inserting -- best-effort dedup, not a DB-enforced constraint.
    idempotency_key: str | None = Field(default=None)
    read_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)  # type: ignore[call-overload]
    email_sent_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)  # type: ignore[call-overload]
    push_sent_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)  # type: ignore[call-overload]


class PushToken(UUIDPKMixin, TimestampMixin, SoftDeleteMixin, SQLModel, table=True):
    __tablename__ = "push_token"

    user_id: UUID = Field(foreign_key="user.id", index=True)
    token: str = Field(unique=True)  # Expo push token
    platform: str  # "ios" | "android"
    device_label: str | None = Field(default=None)
    last_used_at: datetime = Field(sa_type=_TZ_DATETIME)  # type: ignore[call-overload]
