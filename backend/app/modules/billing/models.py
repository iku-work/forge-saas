from datetime import datetime
from uuid import UUID

from sqlalchemy import Column, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models._mixins import SoftDeleteMixin, TimestampMixin, UUIDPKMixin, utcnow

# Every datetime column declared directly in this file (i.e. not inherited
# from TimestampMixin) must use the same tz-aware type as the mixin — see
# app.models._mixins for why a naive TIMESTAMP column breaks asyncpg binds.
_TZ_DATETIME = DateTime(timezone=True)


class Plan(UUIDPKMixin, TimestampMixin, SoftDeleteMixin, SQLModel, table=True):
    __tablename__ = "plan"

    name: str
    slug: str = Field(unique=True, index=True)
    stripe_price_id_monthly: str
    stripe_price_id_yearly: str
    price_monthly: int  # cents
    price_yearly: int  # cents
    # e.g. {"seats": 5, "storage_gb": 10, "api_calls": 10000} — consulted by
    # billing.dependencies.require_feature (T-031).
    features: dict[str, int] = Field(sa_column=Column(JSONB, nullable=False))
    is_active: bool = Field(default=True)


class Subscription(UUIDPKMixin, TimestampMixin, SQLModel, table=True):
    __tablename__ = "subscription"

    org_id: UUID = Field(unique=True, index=True)  # one subscription per org
    plan_id: UUID = Field(foreign_key="plan.id", index=True)
    stripe_subscription_id: str = Field(unique=True)
    status: str  # "trialing" | "active" | "past_due" | "canceled" | "incomplete"
    current_period_start: datetime = Field(sa_type=_TZ_DATETIME)  # type: ignore[call-overload]
    current_period_end: datetime = Field(sa_type=_TZ_DATETIME)  # type: ignore[call-overload]
    cancel_at_period_end: bool = Field(default=False)
    trial_end: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)  # type: ignore[call-overload]


class WebhookEvent(SQLModel, table=True):
    __tablename__ = "webhook_event"

    id: str = Field(primary_key=True)  # Stripe event ID, e.g. "evt_..."
    type: str = Field(index=True)
    payload: dict[str, object] = Field(sa_column=Column(JSONB, nullable=False))
    received_at: datetime = Field(default_factory=utcnow, sa_type=_TZ_DATETIME)  # type: ignore[call-overload]
    processed_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)  # type: ignore[call-overload]
    error: str | None = Field(default=None)
    attempt_count: int = Field(default=0)
