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
    price_monthly: int  # minor units of `currency`
    price_yearly: int  # minor units of `currency`
    # ISO 4217, lowercase, as Stripe reports it on the Price. Carried because
    # the amounts above are meaningless without it: before this column the
    # whole stack assumed USD and hardcoded a "$", so a EUR catalogue
    # advertised "$49" on the pricing page and charged EUR 49 at the till.
    # app.core.sync_plans reads it straight off the resolved price and
    # refuses a plan whose monthly and yearly prices disagree.
    currency: str = Field(default="usd", max_length=3)
    # e.g. {"seats": 5, "storage_gb": 10, "api_calls": 10000} — consulted by
    # billing.dependencies.require_feature (T-031).
    features: dict[str, int] = Field(sa_column=Column(JSONB, nullable=False))
    is_active: bool = Field(default=True)
    # T-132: metered-metric name -> Stripe billing Meter event_name for that
    # dimension, e.g. {"api_calls": "api_calls_meter"} (see
    # https://docs.stripe.com/billing/subscriptions/usage-based/meters --
    # the modern Billing Meters API, not the deprecated
    # SubscriptionItem.create_usage_record). Deliberately its own column
    # rather than a value inside `features` (typed dict[str, int] above, and
    # relied on as such by require_feature's numeric `minimum` comparison) --
    # a meter's event_name is reported against the org's Stripe *customer*
    # (app.workers.jobs.usage.report_usage_to_stripe), not a plan feature
    # count.
    usage_meter_event_names: dict[str, str] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False)
    )
    # Local mirror of the metered Price's Stripe-side `unit_amount_decimal`
    # -- metric -> price per unit in micros (1,000,000 micros = $1 USD), see
    # docs/runbooks/stripe-usage-pricing.md. Estimate-only: Stripe's own
    # Price is authoritative for actual invoicing, this just lets the app
    # compute a local "estimated cost so far" (BillingService.
    # get_usage_summary) without calling Stripe on every render. A metric
    # can appear in usage_meter_event_names without an entry here -- not
    # every tracked dimension is priced.
    usage_prices_micros: dict[str, int] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False)
    )


class Subscription(UUIDPKMixin, TimestampMixin, SQLModel, table=True):
    __tablename__ = "subscription"

    org_id: UUID = Field(unique=True, index=True)  # one subscription per org
    plan_id: UUID = Field(foreign_key="plan.id", index=True)
    stripe_subscription_id: str = Field(unique=True)
    status: str  # "trialing" | "active" | "past_due" | "canceled" | "incomplete"
    current_period_start: datetime = Field(sa_type=_TZ_DATETIME)
    current_period_end: datetime = Field(sa_type=_TZ_DATETIME)
    cancel_at_period_end: bool = Field(default=False)
    trial_end: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)
    # T-141: admin force-assign (app.modules.admin.service.AdminBillingOverrideService)
    # sets these three on top of Stripe's own fields above. billing_interval
    # has no Stripe-webhook equivalent to read it back from (Stripe reports
    # interval per-price, not per-subscription in the webhook payload this
    # app stores) -- it's purely what the admin selected. free_override marks
    # a subscription an admin comp'd without a real Stripe subscription behind
    # it (see stripe_subscription_id's ADMIN_OVERRIDE_PREFIX placeholder in
    # app.modules.admin.service); override_expires_at is when that comp
    # should be reviewed/revert, independent of current_period_end.
    billing_interval: str | None = Field(default=None)
    free_override: bool = Field(default=False)
    override_expires_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)
    # `created` of the newest Stripe *event* whose state is reflected here.
    #
    # Stripe does not promise delivery order, and its retries make disorder
    # likely rather than exotic: an event that failed once is redelivered
    # minutes later, by which time newer ones have already arrived. Without
    # this, a redelivered `payment_failed` lands after the `payment_succeeded`
    # that resolved it and puts a paying customer back into dunning.
    #
    # NULL means "no event has been applied yet" -- every row predating this
    # column, and every row the reconciler wrote (which reads current state
    # straight from the API and is therefore never stale).
    last_stripe_event_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)
    # The same watermark for the *invoice* stream, kept separate on purpose.
    #
    # One shared watermark was wrong, and wrong in the direction that hurts:
    # `customer.subscription.*` and `invoice.payment_*` describe different
    # objects, so their `created` timestamps are not comparable. Creating a
    # subscription whose first charge fails emits both within one second,
    # and Stripe stamped the invoice failure a second *earlier* than the
    # subscription record while delivering it second -- so the shared
    # watermark rejected a perfectly valid payment failure as stale and
    # dunning never started. Observed in production on 2026-09-13.
    #
    # Ordering is meaningful *within* each stream (a redelivered
    # payment_failed must not undo a later payment_succeeded) and meaningless
    # across them, which is exactly what two columns express.
    last_invoice_event_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)


class DunningCampaign(UUIDPKMixin, TimestampMixin, SQLModel, table=True):
    __tablename__ = "dunning_campaign"

    # Not OrgScopedMixin: like Subscription (which this joins through for its
    # org), a dunning campaign is reached via subscription_id, never listed
    # by org_id directly.
    subscription_id: UUID = Field(foreign_key="subscription.id", index=True)
    invoice_id: str
    status: str = Field(default="active")  # "active" | "canceled" | "completed"
    # arq job ids for the day-1/day-3/day-7 steps, in that order -- so
    # DunningService.cancel/complete can revoke whichever haven't fired yet.
    scheduled_job_ids: list[str] = Field(sa_column=Column(JSONB, nullable=False))
    started_at: datetime = Field(default_factory=utcnow, sa_type=_TZ_DATETIME)
    ended_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)


class WebhookEvent(SQLModel, table=True):
    __tablename__ = "webhook_event"

    id: str = Field(primary_key=True)  # Stripe event ID, e.g. "evt_..."
    type: str = Field(index=True)
    payload: dict[str, object] = Field(sa_column=Column(JSONB, nullable=False))
    received_at: datetime = Field(default_factory=utcnow, sa_type=_TZ_DATETIME)
    processed_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)
    error: str | None = Field(default=None)
    attempt_count: int = Field(default=0)
