"""
Billing event processing (T-030) plus checkout/portal redirects and the
Stripe customer lifecycle (T-031). The plan-gating dependency itself
(require_feature) lives in app.modules.billing.dependencies, not here --
see that module's docstring for why it goes straight to the repository
layer instead of through this service.

handle_event operates on the *stored* WebhookEvent row, not a live stripe.Event
object: app.modules.billing.webhooks persists the event before dispatching
(idempotency requires that ordering), and a retried delivery re-reads the
same row from Postgres rather than re-verifying Stripe's signature. Working
off `event.payload` (a plain JSONB dict) keeps this method's behavior
identical on first delivery and on retry.

Checkout/portal session creation calls Stripe's API synchronously in the
request path, unlike §0.9's "slow 3rd-party calls go to ARQ jobs" rule --
deliberately: the response *is* the redirect URL the browser needs
immediately, so there's nothing to hand off to a background job.

T-120: every handler that changes a Subscription's status (upsert, delete,
and the two invoice.payment_* handlers -- a payment success/failure moves
status between active/past_due/canceled, which affects FeatureFlagService's
plan-layer resolution the same way an upsert does) invalidates that org's
feature-flag cache afterward, per T-120's own "Called from: subscription
updates" requirement.

T-130: invoice.payment_failed starts a DunningCampaign (the day-1/day-3/day-7
reminder email sequence); invoice.payment_succeeded and
customer.subscription.deleted both end whatever campaign is active for that
subscription -- completed on a successful payment, canceled on subscription
deletion -- since either means there's nothing left to remind the org about.

T-133: both invoice.* handlers also invalidate InvoiceService's cache for the
affected org, since a status flip (open -> paid, or open -> past_due) changes
what the cached invoice list's `status` field should read.

T-206: every outbound Stripe call below goes through app.core.resilience's
`@resilient("stripe")` and, correspondingly, stripe's own `_async` method
variants rather than their sync equivalents -- a blocking sync call inside an
`async def` would still stall the event loop regardless of the decorator.
Each decorated function makes exactly one real Stripe call so the shared
"stripe" breaker's failure count matches "one breaker failure per outbound
Stripe call", not per higher-level operation; `create_checkout_session`/
`create_portal_session` call `get_or_create_stripe_customer` (its own
decorated unit) and then their own decorated single-call helper, rather than
wrapping the whole method a second time, to avoid nested retry storms.
"""

import logging
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID

import stripe
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import invalidate_org_cache
from app.core.exceptions import ConflictError, DomainError, NotFoundError
from app.core.metrics import FORGE_BILLING_UNAPPLIED_EVENTS_TOTAL
from app.core.resilience import resilient
from app.core.urls import frontend_url
from app.modules.audit.service import AuditService
from app.modules.billing.dunning import DunningService
from app.modules.billing.invoices import InvoiceService
from app.modules.billing.models import Plan, Subscription, WebhookEvent
from app.modules.billing.repository import PlanRepository, SubscriptionRepository
from app.modules.billing.schemas import UsageSummary
from app.modules.billing.usage_service import UsageService
from app.modules.feature_flags.service import FeatureFlagService
from app.modules.tenancy.models import Organization
from app.modules.tenancy.repository import OrganizationRepository
from app.modules.webhooks import service as webhooks
from app.modules.webhooks.events import EventType, SubscriptionPayload

logger = logging.getLogger(__name__)


def _epoch_to_datetime(value: int | None) -> datetime | None:
    return datetime.fromtimestamp(value, tz=UTC) if value is not None else None


# --- reading Stripe objects across the Basil rename ---------------------------------
#
# Stripe's `2025-03-31.basil` API version moved two fields this integration
# depends on, and moved them *silently*: the old keys are simply absent, so
# code that reads them either raises KeyError or -- worse -- takes a `.get()`
# default and does nothing. Both happened here in production on 2026-09-12,
# against an account on 2025-08-27.basil:
#
#   subscription.current_period_{start,end}
#       -> subscription.items.data[].current_period_{start,end}
#          A subscription's items can now bill on different cadences, so the
#          period is a property of the item, not the subscription.
#
#   invoice.subscription
#       -> invoice.parent.subscription_details.subscription
#          `parent` is a discriminated union; `type` is "subscription_details"
#          for a subscription invoice and something else (e.g.
#          "quote_details") otherwise, with the non-matching members null.
#
# Both readers below prefer the new location and fall back to the old one.
# The fallback is not dead weight: this repo pins no `stripe.api_version`, so
# the shape is whatever the *account* is set to, and an account created
# before Basil still sends the old one. Reading both means the integration
# does not break in either direction when that setting changes.


def _subscription_period(obj: dict[str, Any]) -> tuple[int, int]:
    """The current billing period of `obj`, a Stripe Subscription, as epochs.

    Read from the same item the plan is resolved from (the first), so the
    period and the plan always describe the same thing. A multi-item
    subscription with divergent cadences is out of scope here -- checkout
    creates exactly one item -- and picking item 0 for both is at least
    coherent, where mixing item 0's price with, say, the latest end date
    across items would not be.
    """
    items = obj.get("items", {}).get("data") or [{}]
    first = items[0]
    for key in ("current_period_start", "current_period_end"):
        if key not in first and key not in obj:
            # Not a warn-and-return: the columns are NOT NULL and this is a
            # subscription somebody has paid for. Raising leaves the event
            # unprocessed with the reason recorded, and Stripe retries.
            msg = f"subscription {obj.get('id')!r} has no {key} on its item or itself"
            raise KeyError(msg)
    return (
        first.get("current_period_start", obj.get("current_period_start")),
        first.get("current_period_end", obj.get("current_period_end")),
    )


# A subscription in one of these states is one Stripe will still bill for,
# so starting a *second* checkout on top of it buys the customer a duplicate
# rather than an upgrade. `past_due` is deliberately included: the money is
# still owed and the subscription still exists, and the fix for it is the
# portal (update the card), never a second subscription. `incomplete` is not
# -- that is a checkout that never completed, and refusing to let someone
# retry it would strand them.
_LIVE_SUBSCRIPTION_STATUSES = frozenset({"active", "trialing", "past_due"})


def _invoice_subscription_id(obj: dict[str, Any]) -> str | None:
    """The subscription an Invoice belongs to, or None if it is not one."""
    parent = obj.get("parent") or {}
    details = parent.get("subscription_details") or {}
    subscription = details.get("subscription") or obj.get("subscription")
    # Expanded, the field is the object rather than its id.
    if isinstance(subscription, dict):
        return cast("str | None", subscription.get("id"))
    return cast("str | None", subscription)


@resilient("stripe")
async def _create_checkout_session_stripe(
    *, customer_id: str, price_id: str, org_id: str, return_to: str
) -> stripe.checkout.Session:
    return await stripe.checkout.Session.create_async(
        customer=customer_id,
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=f"{return_to}?checkout=success",
        cancel_url=f"{return_to}?checkout=canceled",
        metadata={"org_id": org_id},
        # The webhook handler (_handle_subscription_upserted) reads org_id
        # off the *Subscription* object's metadata, not the Checkout
        # Session's -- subscription_data.metadata is what Stripe copies onto
        # the Subscription it creates.
        subscription_data={"metadata": {"org_id": org_id}},
    )


@resilient("stripe")
async def _create_portal_session_stripe(
    *, customer_id: str, return_to: str
) -> stripe.billing_portal.Session:
    return await stripe.billing_portal.Session.create_async(
        customer=customer_id,
        return_url=return_to,
    )


def _billing_page_url(locale: str | None) -> str:
    """Where Stripe sends the customer back to.

    Built with frontend_url, not by hand. The hand-built version was
    `https://{DOMAIN}/billing`, which was wrong three times over once T-233
    put a locale segment on every route: the path is /settings/billing, it
    needs a locale in front of it, and DOMAIN is the bare host where
    FRONTEND_URL is the origin the app is actually served on. A customer who
    had just paid landed on the SPA's not-found page.
    """
    return frontend_url("/settings/billing", locale)


def _will_not_renew(obj: dict[str, Any]) -> bool:
    """Whether this Stripe Subscription is scheduled to stop.

    The third field Basil moved, and the one with a customer-visible lie in
    it. Cancelling at period end used to set `cancel_at_period_end: true`;
    under `2025-08-27.basil` that flag stays **False** and the truth lands in
    `cancel_at`, the timestamp the subscription terminates at. Observed on
    two real production subscriptions on 2026-09-13:

        status               = active
        cancel_at_period_end = False        <- reading only this says "renews"
        cancel_at            = 2026-10-12   <- it does not
        cancellation_details = {'reason': 'cancellation_requested'}

    Reading the old flag alone told a customer who had just cancelled that
    their subscription would renew. Either field means "will not renew", so
    both are read -- which also keeps a pre-Basil account correct.
    """
    return bool(obj.get("cancel_at_period_end")) or obj.get("cancel_at") is not None


def _event_created_at(payload: dict[str, Any]) -> datetime | None:
    """When Stripe generated this event, from the envelope's own `created`."""
    return _epoch_to_datetime(payload.get("created"))


class BillingService:
    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self._session = session
        self._redis = redis
        self._plans = PlanRepository(session)
        self._subscriptions = SubscriptionRepository(session)
        self._audit = AuditService(session)
        self._flags = FeatureFlagService(session, redis)
        self._dunning = DunningService(session)
        self._invoices = InvoiceService(session, redis)
        stripe.api_key = get_settings().STRIPE_SECRET_KEY

    async def handle_event(self, event: WebhookEvent) -> None:
        payload = cast("dict[str, Any]", event.payload)
        # Stripe promises delivery, not order, and its own retries make
        # disorder the common case rather than an exotic one: an event that
        # failed once is redelivered minutes later, behind everything that
        # arrived in the meantime. Each handler compares this against the
        # row's last_stripe_event_at before writing.
        at = _event_created_at(payload)
        match event.type:
            case "customer.subscription.created" | "customer.subscription.updated":
                await self.apply_subscription_snapshot(payload["data"]["object"], event_created=at)
            case "customer.subscription.deleted":
                await self._handle_subscription_deleted(payload, at)
            case "invoice.payment_succeeded":
                await self._handle_invoice_payment_succeeded(payload, at)
            case "invoice.payment_failed":
                await self._handle_invoice_payment_failed(payload, at)
            case _:
                logger.info("billing.webhook_ignored: type=%s", event.type)

    async def _is_stale(
        self, subscription: Subscription | None, event_created: datetime | None
    ) -> bool:
        """True when this event describes a state older than the row already has.

        `event_created is None` means the caller has no ordering information
        to offer -- an internal caller, or a fixture predating the column.
        The reconciler passes `utcnow()` rather than None: its read *is*
        current state, so every event generated before it is older, and
        advancing the watermark is what stops a queued redelivery from
        undoing the repair.
        """
        if subscription is None or event_created is None:
            return False
        last = subscription.last_stripe_event_at
        if last is None or event_created >= last:
            return False
        await self._record_unapplied(
            "stale_event",
            org_id=subscription.org_id,
            stripe_subscription_id=subscription.stripe_subscription_id,
            detail=(
                f"event generated {event_created.isoformat()} is older than the "
                f"state already applied from {last.isoformat()}"
            ),
        )
        return True

    @staticmethod
    def _mark_applied(subscription: Subscription, event_created: datetime | None) -> None:
        if event_created is not None and (
            subscription.last_stripe_event_at is None
            or event_created > subscription.last_stripe_event_at
        ):
            subscription.last_stripe_event_at = event_created

    async def _record_unapplied(
        self,
        reason: str,
        *,
        org_id: UUID | None,
        stripe_subscription_id: str | None,
        detail: str,
    ) -> None:
        """One place for "understood the event, did not apply it".

        Each of these used to be a bare `logger.warning(...); return`. That
        is the quietest possible way to strand a paying customer on the free
        tier: the endpoint answers 200, Stripe considers the event
        delivered, nothing is at ERROR, and the only trace is a log line
        nobody greps for. A counter makes it alertable and an audit row
        makes it explicable months later, when the question is "why was this
        org never upgraded?".
        """
        FORGE_BILLING_UNAPPLIED_EVENTS_TOTAL.labels(reason=reason).inc()
        logger.warning(
            "billing.event_not_applied: reason=%s subscription=%s org=%s detail=%s",
            reason,
            stripe_subscription_id,
            org_id,
            detail,
        )
        if org_id is not None:
            await self._audit.log(
                action=f"billing.event_not_applied.{reason}",
                resource_type="subscription",
                resource_id=stripe_subscription_id,
                org_id=org_id,
                diff={"reason": reason, "detail": detail},
                severity="elevated",
            )

    async def apply_subscription_snapshot(
        self, obj: dict[str, Any], *, event_created: datetime | None = None
    ) -> None:
        """Bring local state in line with one Stripe Subscription object.

        Public because the webhook is not the only way this arrives:
        app.workers.jobs.billing.reconcile_subscriptions reads the same
        objects straight from the API and applies them through here. Sharing
        the function rather than the rules is the point -- a reconciler that
        re-implemented plan lookup, the Basil period read and the duplicate
        check would drift from the webhook the first time any of them
        changed, and drift is exactly what it exists to remove.
        """
        org_id = obj.get("metadata", {}).get("org_id")
        if org_id is None:
            await self._record_unapplied(
                "missing_org_id",
                org_id=None,
                stripe_subscription_id=obj.get("id"),
                detail="subscription metadata carried no org_id",
            )
            return

        org_uuid = UUID(org_id)
        price_id = obj["items"]["data"][0]["price"]["id"]
        plan = await self._plans.get_by_stripe_price_id(price_id)
        if plan is None:
            await self._record_unapplied(
                "unknown_price",
                org_id=org_uuid,
                stripe_subscription_id=obj.get("id"),
                detail=(
                    f"price {price_id} matches no plan; run `make sync-plans` if this price is new"
                ),
            )
            return

        # A second Stripe subscription on an org that already has a live one.
        #
        # `subscription.org_id` is unique, so `upsert` -- which looks the row
        # up by *Stripe* id -- would take the INSERT path and raise
        # UniqueViolation. That is not a crash worth having: the endpoint
        # would 500, Stripe would retry into the same constraint forever, and
        # the event would never clear. Verified against the real schema.
        #
        # So it is recorded and skipped rather than applied. Not applied,
        # because there is no safe automatic answer: the customer already has
        # service from the first subscription, and silently switching the org
        # onto the second one could move them to whichever of the two an
        # operator is about to cancel and refund. A human has to decide which
        # survives; `_record_unapplied` is how they find out they need to.
        #
        # A *dead* existing row is different -- that is an ordinary
        # resubscribe, and adopting the new subscription is exactly right.
        existing = await self._subscriptions.get_by_org_id(org_uuid)
        if (
            existing is not None
            and existing.stripe_subscription_id == obj["id"]
            and await self._is_stale(existing, event_created)
        ):
            return
        if (
            existing is not None
            and existing.stripe_subscription_id != obj["id"]
            and existing.status in _LIVE_SUBSCRIPTION_STATUSES
        ):
            await self._record_unapplied(
                "duplicate_subscription",
                org_id=org_uuid,
                stripe_subscription_id=obj["id"],
                detail=(
                    f"org already has live subscription "
                    f"{existing.stripe_subscription_id} ({existing.status}); "
                    "cancel and refund one of the two in Stripe"
                ),
            )
            return

        period_start, period_end = _subscription_period(obj)
        subscription = await self._subscriptions.upsert(
            org_id=org_uuid,
            plan_id=plan.id,
            stripe_subscription_id=obj["id"],
            status=obj["status"],
            current_period_start=cast("datetime", _epoch_to_datetime(period_start)),
            current_period_end=cast("datetime", _epoch_to_datetime(period_end)),
            cancel_at_period_end=_will_not_renew(obj),
            trial_end=_epoch_to_datetime(obj.get("trial_end")),
        )
        self._mark_applied(subscription, event_created)
        await self._flags.invalidate(org_uuid)
        # T-320. The one billing event worth publishing: a consumer that
        # provisions on plan change needs it, and everything else Stripe
        # sends is either an implementation detail of this integration or
        # something the consumer should be reading from Stripe itself.
        await webhooks.emit(
            self._session,
            org_uuid,
            EventType.SUBSCRIPTION_UPDATED,
            SubscriptionPayload(
                subscription_id=subscription.id, plan_slug=plan.slug, status=subscription.status
            ),
        )

    async def _handle_subscription_deleted(
        self, payload: dict[str, Any], event_created: datetime | None = None
    ) -> None:
        obj = payload["data"]["object"]
        if await self._is_stale(
            await self._subscriptions.get_by_stripe_subscription_id(obj["id"]), event_created
        ):
            return
        subscription = await self._subscriptions.set_status_by_stripe_subscription_id(
            obj["id"], "canceled"
        )
        if subscription is not None:
            self._mark_applied(subscription, event_created)
            await self._dunning.cancel(subscription.id)
            await self._flags.invalidate(subscription.org_id)

    async def _handle_invoice_payment_succeeded(
        self, payload: dict[str, Any], event_created: datetime | None = None
    ) -> None:
        obj = payload["data"]["object"]
        subscription_id = _invoice_subscription_id(obj)
        if subscription_id is None:
            return
        if await self._is_stale(
            await self._subscriptions.get_by_stripe_subscription_id(subscription_id),
            event_created,
        ):
            return
        subscription = await self._subscriptions.set_status_by_stripe_subscription_id(
            subscription_id, "active"
        )
        if subscription is not None:
            self._mark_applied(subscription, event_created)
            await self._dunning.complete(subscription.id)
            await self._flags.invalidate(subscription.org_id)
            await self._invoices.invalidate(subscription.org_id)

    async def _handle_invoice_payment_failed(
        self, payload: dict[str, Any], event_created: datetime | None = None
    ) -> None:
        obj = payload["data"]["object"]
        subscription_id = _invoice_subscription_id(obj)
        if subscription_id is None:
            return
        if await self._is_stale(
            await self._subscriptions.get_by_stripe_subscription_id(subscription_id),
            event_created,
        ):
            return
        subscription = await self._subscriptions.set_status_by_stripe_subscription_id(
            subscription_id, "past_due"
        )
        if subscription is not None:
            self._mark_applied(subscription, event_created)
            await self._audit.log(
                action="billing.payment_failed",
                resource_type="subscription",
                resource_id=str(subscription.id),
                org_id=subscription.org_id,
            )
            await self._dunning.start(subscription.id, obj["id"])
            await self._flags.invalidate(subscription.org_id)
            await self._invoices.invalidate(subscription.org_id)

    # --- checkout / portal / customer (T-031) ----------------------------------------

    @resilient("stripe")
    async def get_or_create_stripe_customer(self, org: Organization) -> str:
        if org.stripe_customer_id is not None:
            return org.stripe_customer_id

        customer = await stripe.Customer.create_async(
            name=org.name, metadata={"org_id": str(org.id)}
        )
        # Written through the repository, not by mutating `org` and flushing:
        # since T-240 this org may have been hydrated from the cache, and a
        # detached instance's mutation is silently not persisted -- which
        # would mean a brand-new Stripe customer created on every checkout.
        # The local copy is updated too, for the rest of this request.
        await OrganizationRepository(self._session).update(org.id, stripe_customer_id=customer.id)
        org.stripe_customer_id = customer.id
        await invalidate_org_cache(self._redis, org.id)
        return customer.id

    async def create_checkout_session(
        self,
        org: Organization,
        plan_slug: str,
        interval: Literal["monthly", "yearly"],
        locale: str | None = None,
    ) -> str:
        plan = await self._plans.get_by_slug(plan_slug)
        if plan is None:
            raise NotFoundError(
                "errors.plan_not_found",
                fallback=f"Plan '{plan_slug}' not found",
                params={"plan_slug": plan_slug},
            )

        price_id = (
            plan.stripe_price_id_monthly if interval == "monthly" else plan.stripe_price_id_yearly
        )
        # An org that already pays for something must not be able to buy a
        # second subscription. Two of these existed on one org in production
        # on 2026-09-12 -- two charges, one plan -- because this method had
        # no such check and a customer could simply open checkout twice (or
        # once more while the first subscription's webhook was still in
        # flight). Stripe is happy to create both; `subscription.org_id` is
        # unique, so the second one's webhook could not even be recorded.
        #
        # The portal, not a bare refusal: "you already have a subscription"
        # with no way forward is a dead end on the one page where the
        # customer came to spend money. The portal is where Stripe expects
        # an existing customer to change plan or cancel, and the URL rides
        # along in `details` so the client can send them straight there.
        existing = await self._subscriptions.get_by_org_id(org.id)
        if existing is not None and existing.status in _LIVE_SUBSCRIPTION_STATUSES:
            raise ConflictError(
                "errors.subscription_already_active",
                fallback="This organization already has an active subscription.",
                details={"portal_url": await self.create_portal_session(org, locale)},
            )

        customer_id = await self.get_or_create_stripe_customer(org)

        checkout_session = await _create_checkout_session_stripe(
            customer_id=customer_id,
            price_id=price_id,
            org_id=str(org.id),
            return_to=_billing_page_url(locale),
        )
        if checkout_session.url is None:
            raise DomainError(
                "errors.checkout_url_missing", fallback="Stripe did not return a checkout URL"
            )
        return checkout_session.url

    async def create_portal_session(self, org: Organization, locale: str | None = None) -> str:
        customer_id = await self.get_or_create_stripe_customer(org)

        portal_session = await _create_portal_session_stripe(
            customer_id=customer_id, return_to=_billing_page_url(locale)
        )
        return portal_session.url

    async def get_subscription(self, org_id: UUID) -> Subscription | None:
        return await self._subscriptions.get_by_org_id(org_id)

    async def list_active_plans(self) -> list[Plan]:
        return await self._plans.list_active()

    async def get_usage_summary(self, org_id: UUID) -> list[UsageSummary]:
        """Estimated usage cost per metered dimension for the org's current
        billing period -- see docs/runbooks/stripe-usage-pricing.md.
        Iterates Plan.usage_meter_event_names (the metrics actually tracked/
        reported), not usage_prices_micros, since a metric can be tracked
        without being priced locally."""
        subscription = await self._subscriptions.get_by_org_id(org_id)
        if subscription is None:
            return []
        plan = await self._plans.get_by_id(subscription.plan_id)
        if plan is None:
            return []

        usage = UsageService(self._session, org_id)
        summaries: list[UsageSummary] = []
        for metric in plan.usage_meter_event_names:
            quantity = await usage.aggregate(
                metric=metric,
                since=subscription.current_period_start,
                until=subscription.current_period_end,
            )
            unit_price_micros = plan.usage_prices_micros.get(metric)
            summaries.append(
                UsageSummary(
                    metric=metric,
                    quantity=quantity,
                    unit_price_micros=unit_price_micros,
                    estimated_cost_micros=(
                        quantity * unit_price_micros if unit_price_micros is not None else None
                    ),
                )
            )
        return summaries
