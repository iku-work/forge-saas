"""
Request/response schemas for the billing module (T-031). Distinct from the
SQLModel table classes in app.modules.billing.models per §0.4 -- in
particular SubscriptionPublic and PlanPublic omit every Stripe id
(stripe_subscription_id, stripe_price_id_monthly/yearly), which is
implementation detail the frontend has no use for and shouldn't see.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field

from app.core.schemas import AppBaseModel


class CheckoutSessionRequest(AppBaseModel):
    plan_slug: str = Field(min_length=1, max_length=255)
    interval: Literal["monthly", "yearly"]


class CheckoutSessionResponse(AppBaseModel):
    url: str


class PortalSessionResponse(AppBaseModel):
    url: str


class SubscriptionPublic(AppBaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    plan_id: UUID
    status: str
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool
    trial_end: datetime | None


class PlanPublic(AppBaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    price_monthly: int
    price_yearly: int
    # Without this the client has to guess, and both of ours guessed "USD".
    currency: str
    features: dict[str, int]


# One metered dimension's usage + estimated cost for the org's current
# billing period (BillingService.get_usage_summary). unit_price_micros/
# estimated_cost_micros are None when the plan tracks this metric
# (Plan.usage_meter_event_names) without pricing it locally
# (Plan.usage_prices_micros) -- see docs/runbooks/stripe-usage-pricing.md.
# Estimate only: Stripe's own metered Price is authoritative for what
# actually gets invoiced.
class UsageSummary(AppBaseModel):
    metric: str
    quantity: int
    unit_price_micros: int | None
    estimated_cost_micros: int | None
