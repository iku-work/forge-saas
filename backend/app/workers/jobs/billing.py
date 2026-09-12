"""
ARQ job for the T-130 dunning email sequence: DunningService.start (app.
modules.billing.dunning) enqueues one call to send_dunning_email per
day-1/day-3/day-7 step, each deferred by the matching delay -- this module is
what actually runs when arq's scheduled time for a step arrives.

Every invocation re-reads the DunningCampaign row rather than trusting its
enqueue-time snapshot: by the time a day-3/day-7 step's delay elapses, the
campaign may already be canceled (payment succeeded) or completed
(subscription deleted since). app.core.jobs.revoke_job tries to pull a
canceled/completed campaign's remaining steps out of the queue at the moment
it closes, but a race between that revoke and this job's own dequeue is
possible, so the campaign's *current* status is checked here too, as the
authoritative guard.

Idempotency (agents.md T-130: "re-running same step does not re-send"): the
audit log doubles as the per-step send marker, keyed on
f"{campaign_id}:{step}" -- see app.modules.audit.service.AuditService.
has_logged. An ARQ retry of an already-completed step is therefore a no-op
rather than a duplicate email.

Recipients are every OWNER member of the subscription's org (T-130 spec:
"addressed to org OWNER memberships") -- a raw join through Membership
rather than a new repository method, the same call app.workers.jobs.gdpr
makes for its own cross-module reads.

T-234: an org's owners do not all speak the same language, so nothing about
a recipient's message may be computed above the loop over `owners`. The
day-1/day-3 steps hand the whole fan-out to NotificationService.send_many,
which resolves locale, preferences and name per recipient (and adds the
in-app + push channels those preferences ask for); day-7 keeps its own loop,
with the CTA link built per owner from `owner.locale` rather than once from
settings.DOMAIN. Everything shared above the loop is now locale-independent
data only -- the org name, the invoice amount, the plan name.

The day-1 email needs the failed invoice's amount, which DunningCampaign
doesn't store (only invoice_id) -- fetched from Stripe at send time instead
of threaded through from the webhook payload, since a background job talking
to a slow third party is exactly the case app.modules.billing.service's own
docstring calls out for *not* doing synchronously in the request path.

T-131 adds two more scheduled (APScheduler, not ARQ-deferred) jobs, wired
into app.workers.scheduler at 09:00 and 10:00 UTC respectively:

- notify_expiring_trials: sends the 3-day and 1-day trial-ending warning to
  every OWNER, once per subscription per horizon. "Once" is enforced the
  same way as the dunning steps above -- an audit_event marker
  (trial.warning_sent_3d / trial.warning_sent_1d) is checked before sending
  and written after. The 3-day/1-day windows are each a narrow slice (see
  SubscriptionRepository.list_trialing_with_trial_end_between) so a daily
  tick catches every trial_end that lands in it exactly once, regardless of
  what time of day trial_end actually falls at.
- expire_trials_to_inactive: for trials that have already ended with no
  Stripe payment method on file, transitions the subscription to "canceled"
  and sends a "trial expired" email. Whether a payment method exists isn't
  local state -- Stripe is the source of truth for cards on file -- so this
  calls the Stripe API per candidate, same trade-off as the day-1 dunning
  email above.
"""

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import jobs
from app.core.config import get_settings
from app.core.db import async_session_factory
from app.core.metrics import (
    FORGE_BILLING_RECONCILED_TOTAL,
)
from app.core.resilience import resilient
from app.core.tracing import trace_job
from app.core.urls import frontend_url
from app.models._mixins import utcnow
from app.modules.audit.service import AuditService
from app.modules.auth.models import User
from app.modules.auth.permissions import MemberRole
from app.modules.billing.models import DunningCampaign, Plan, Subscription
from app.modules.billing.repository import (
    DunningCampaignRepository,
    PlanRepository,
    SubscriptionRepository,
)
from app.modules.billing.service import BillingService
from app.modules.feature_flags.service import FeatureFlagService
from app.modules.notifications.service import NotificationService
from app.modules.tenancy.models import Membership, Organization
from app.modules.tenancy.repository import OrganizationRepository

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

_TEMPLATE_BY_STEP = {
    "day1": "payment_failed",
    "day3": "subscription_expiring",
    "day7": "subscription_suspended",
}

# T-234: the two steps that map onto a notification type go through
# NotificationService instead of enqueuing send_email directly, so the org's
# owners also get the in-app + push channels their preferences ask for --
# each rendered in that owner's own language. The email still goes out, from
# the notification's own email channel and through the same rich template
# _TEMPLATE_BY_STEP names, so a default-preferences owner sees no change.
#
# The one behaviour change: an owner who has turned the email channel *off*
# for "A payment fails" on the notification-preferences page now stops
# getting these two dunning emails, where before the page said one thing and
# the job did another. If a product needs dunning to be unconditional
# regardless of preference, drop the step from this map and give it its own
# loop like day7's below -- don't leave the toggle showing.
#
# day7 has no matching type (there is no "subscription_suspended"
# notification in T-101.5's DEFAULT_PREFERENCES) and stays email-only.
_NOTIFICATION_TYPE_BY_STEP = {
    "day1": "payment_failed",
    "day3": "subscription_expiring",
}

_BILLING_PATH = "/settings/billing"

_USD_SYMBOL_THRESHOLD_CURRENCY = "usd"


async def _owners_for_org(session: AsyncSession, org_id: UUID) -> list[User]:
    m = cast("Any", Membership)
    u = cast("Any", User)
    stmt = (
        select(User)
        .join(Membership, m.user_id == u.id)
        .where(m.org_id == org_id, m.role == MemberRole.OWNER)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


def _format_amount(amount_cents: int, currency: str) -> str:
    amount = f"{amount_cents / 100:.2f}"
    if currency.lower() == _USD_SYMBOL_THRESHOLD_CURRENCY:
        return f"${amount}"
    return f"{amount} {currency.upper()}"


def _step_context(
    step: str,
    *,
    campaign: DunningCampaign,
    plan: Plan,
    subscription: Subscription,
) -> dict[str, Any]:
    """The step-specific half of the message data -- locale-independent by
    construction (an amount, a plan name, an ISO date), which is what makes
    it safe to compute once above the per-recipient fan-out below."""
    if step == "day1":
        stripe.api_key = get_settings().STRIPE_SECRET_KEY
        invoice = stripe.Invoice.retrieve(campaign.invoice_id)
        return {"amount": _format_amount(invoice.amount_due, invoice.currency)}
    if step == "day3":
        return {
            "plan_name": plan.name,
            "expires_at": subscription.current_period_end.date().isoformat(),
        }
    return {"plan_name": plan.name}


@trace_job
async def send_dunning_email(
    ctx: dict[str, Any],  # ARQ calling convention; registered in app.workers.main
    *,
    campaign_id: UUID,
    step: str,
) -> None:
    async with async_session_factory() as session:
        campaigns = DunningCampaignRepository(session)
        campaign = await campaigns.get_by_id(campaign_id)
        if campaign is None or campaign.status != "active":
            logger.info(
                "send_dunning_email: campaign_id=%s step=%s is not active, skipping",
                campaign_id,
                step,
            )
            return

        audit = AuditService(session)
        resource_id = f"{campaign_id}:{step}"
        if await audit.has_logged(action="billing.dunning_email_sent", resource_id=resource_id):
            logger.info(
                "send_dunning_email: campaign_id=%s step=%s already sent, skipping",
                campaign_id,
                step,
            )
            return

        subscriptions = SubscriptionRepository(session)
        subscription = await subscriptions.get_by_id(campaign.subscription_id)
        if subscription is None:
            return

        org = await session.get(Organization, subscription.org_id)
        plan = await PlanRepository(session).get_by_id(subscription.plan_id)
        if org is None or plan is None:
            return

        owners = await _owners_for_org(session, subscription.org_id)
        context_extra = _step_context(step, campaign=campaign, plan=plan, subscription=subscription)

        notification_type = _NOTIFICATION_TYPE_BY_STEP.get(step)
        if notification_type is None:
            # day7 (subscription_suspended) has no notification type of its
            # own -- email only, addressed per owner, as before.
            for owner in owners:
                await jobs.enqueue_job(
                    "send_email",
                    to=owner.email,
                    template=_TEMPLATE_BY_STEP[step],
                    locale=owner.locale,
                    context={
                        "user_name": owner.full_name,
                        "org_name": org.name,
                        "billing_url": frontend_url(_BILLING_PATH, owner.locale),
                        **context_extra,
                    },
                )
        else:
            await NotificationService(session, ctx["redis"]).send_many(
                user_ids=[owner.id for owner in owners],
                org_id=subscription.org_id,
                type=notification_type,
                params={"org_name": org.name, **context_extra},
                action_url=_BILLING_PATH,
                idempotency_key=f"dunning:{campaign_id}:{step}",
            )

        await audit.log(
            action="billing.dunning_email_sent",
            resource_type="dunning_campaign",
            resource_id=resource_id,
            org_id=subscription.org_id,
            diff={"step": step},
        )

        if step == "day7":
            if subscription.status == "past_due":
                subscription.status = "canceled"
                # T-240: the downgrade the whole dunning sequence exists to
                # end in. Without this the org keeps its old entitlements
                # (cached plan + feature flags) for the rest of the TTL.
                await FeatureFlagService(session, ctx["redis"]).invalidate(subscription.org_id)
            await campaigns.close(campaign, status="completed")

        await session.commit()


_TRIAL_WARNING_WINDOW = timedelta(hours=1)
_TRIAL_WARNING_HORIZONS: tuple[tuple[timedelta, str], ...] = (
    (timedelta(days=3), "trial.warning_sent_3d"),
    (timedelta(days=1), "trial.warning_sent_1d"),
)


async def _send_trial_email(
    session: AsyncSession,
    *,
    org: Organization,
    template: str,
    context_extra: dict[str, Any],
) -> None:
    owners = await _owners_for_org(session, org.id)
    for owner in owners:
        await jobs.enqueue_job(
            "send_email",
            to=owner.email,
            template=template,
            locale=owner.locale,
            context={
                "user_name": owner.full_name,
                "org_name": org.name,
                # Inside the loop, not above it: the locale segment is the
                # recipient's, exactly like the body this link sits in
                # (T-233/T-234).
                "billing_url": frontend_url(_BILLING_PATH, owner.locale),
                **context_extra,
            },
        )


async def notify_expiring_trials(
    ctx: dict[str, Any],  # noqa: ARG001 -- APScheduler calling convention; see app.workers.scheduler
) -> None:
    now = utcnow()
    async with async_session_factory() as session:
        subscriptions = SubscriptionRepository(session)
        audit = AuditService(session)

        for horizon, action in _TRIAL_WARNING_HORIZONS:
            start = now + horizon
            end = start + _TRIAL_WARNING_WINDOW
            for subscription in await subscriptions.list_trialing_with_trial_end_between(
                start, end
            ):
                resource_id = str(subscription.id)
                if await audit.has_logged(action=action, resource_id=resource_id):
                    continue

                org = await session.get(Organization, subscription.org_id)
                if org is None or subscription.trial_end is None:
                    continue

                await _send_trial_email(
                    session,
                    org=org,
                    template="trial_ending",
                    context_extra={"trial_end_date": subscription.trial_end.date().isoformat()},
                )
                await audit.log(
                    action=action,
                    resource_type="subscription",
                    resource_id=resource_id,
                    org_id=subscription.org_id,
                )

        await session.commit()


def _has_payment_method(customer_id: str | None) -> bool:
    if not customer_id:
        return False
    stripe.api_key = get_settings().STRIPE_SECRET_KEY
    customer = stripe.Customer.retrieve(customer_id)
    if getattr(customer, "deleted", False):
        return False
    invoice_settings = getattr(customer, "invoice_settings", None)
    default_payment_method = (
        getattr(invoice_settings, "default_payment_method", None) if invoice_settings else None
    )
    return bool(getattr(customer, "default_source", None)) or bool(default_payment_method)


async def expire_trials_to_inactive(
    ctx: dict[str, Any],  # APScheduler calling convention; see app.workers.scheduler
) -> None:
    now = utcnow()
    redis: Redis | None = ctx.get("redis")
    async with async_session_factory() as session:
        subscriptions = SubscriptionRepository(session)
        audit = AuditService(session)

        for subscription in await subscriptions.list_trialing_expired(now):
            org = await session.get(Organization, subscription.org_id)
            if org is None:
                continue
            if _has_payment_method(org.stripe_customer_id):
                # Stripe has a card on file -- its own trial-end billing
                # attempt (and the resulting webhook, T-030) is the
                # authoritative state transition, not this safety-net job.
                continue

            resource_id = str(subscription.id)
            if not await audit.has_logged(
                action="billing.trial_expired_email_sent", resource_id=resource_id
            ):
                await _send_trial_email(
                    session,
                    org=org,
                    template="trial_expired",
                    context_extra={},
                )
                await audit.log(
                    action="billing.trial_expired_email_sent",
                    resource_type="subscription",
                    resource_id=resource_id,
                    org_id=subscription.org_id,
                )

            subscription.status = "canceled"
            if redis is not None:
                # Same downgrade, different trigger (T-240) -- see
                # send_dunning_email's day7 branch.
                await FeatureFlagService(session, redis).invalidate(subscription.org_id)

        await session.commit()


# --- reconciliation ---------------------------------------------------------------------
#
# Webhooks are not a reliable transport, and this deployment learned it the
# hard way on 2026-09-12: a bug made the endpoint return 500, Stripe's
# retries ran out, and a customer who had paid sat on the free tier with
# nothing anywhere saying so. Stripe also disables an endpoint that keeps
# failing, at which point *every* subsequent event is lost rather than
# retried -- and a second payment produced no local record at all.
#
# So local state is periodically re-derived from Stripe, which is the system
# of record for what somebody is actually being charged. Anything a missed
# webhook would have done, this does on the next tick.

_RECONCILE_PAGE_SIZE = 100

# Statuses Stripe will still bill for. Kept in step with
# app.modules.billing.service._LIVE_SUBSCRIPTION_STATUSES, which is the same
# question asked at checkout time.
_LIVE_STATUSES = frozenset({"active", "trialing", "past_due"})


@resilient("stripe")
async def _list_stripe_subscriptions(customer_id: str) -> list[dict[str, Any]]:
    # status="all" rather than the default (live only): a subscription that
    # was cancelled while the endpoint was down has to be seen, or the local
    # row keeps granting a plan nobody pays for.
    listing = await stripe.Subscription.list_async(customer=customer_id, status="all", limit=100)
    return [item.to_dict(for_json=True) for item in listing.data]


async def reconcile_subscriptions(
    ctx: dict[str, Any],  # APScheduler calling convention; see app.workers.scheduler
) -> None:
    """Re-derive every org's subscription from Stripe and repair the drift."""
    redis: Redis | None = ctx.get("redis")
    if redis is None or not get_settings().STRIPE_SECRET_KEY:
        logger.info("billing.reconcile_skipped: no redis or no Stripe key configured")
        return

    stripe.api_key = get_settings().STRIPE_SECRET_KEY
    checked = 0
    repaired = 0
    after_id: UUID | None = None

    while True:
        async with async_session_factory() as session:
            orgs = await OrganizationRepository(session).list_with_stripe_customer(
                limit=_RECONCILE_PAGE_SIZE, after_id=after_id
            )
            if not orgs:
                break
            after_id = orgs[-1].id

            for org in orgs:
                checked += 1
                if await _reconcile_one_org(session, redis, org):
                    repaired += 1
            await session.commit()

    logger.info("billing.reconcile_done: checked=%d repaired=%d", checked, repaired)


async def _reconcile_one_org(session: AsyncSession, redis: "Redis", org: Organization) -> bool:
    """Returns True when this org's local state had to be changed."""
    customer_id = cast("str", org.stripe_customer_id)
    try:
        remote = await _list_stripe_subscriptions(customer_id)
    except Exception:
        # One unreachable customer must not abandon the rest of the sweep --
        # the next tick will retry this org anyway.
        logger.exception("billing.reconcile_customer_failed: org=%s", org.id)
        return False

    live = [s for s in remote if s.get("status") in _LIVE_STATUSES]
    subscriptions = SubscriptionRepository(session)
    local = await subscriptions.get_by_org_id(org.id)
    service = BillingService(session, redis)

    if not live:
        # Nothing billable at Stripe. A local row that still claims to be
        # live is granting a plan nobody is paying for.
        if local is not None and local.status in _LIVE_STATUSES:
            logger.warning(
                "billing.reconcile_repair: org=%s local=%s -> canceled (no live Stripe sub)",
                org.id,
                local.status,
            )
            local.status = "canceled"
            await FeatureFlagService(session, redis).invalidate(org.id)
            FORGE_BILLING_RECONCILED_TOTAL.labels(action="canceled").inc()
            return True
        return False

    # More than one live subscription is a duplicate charge, not drift, and
    # picking one automatically could strand the customer on whichever an
    # operator is about to refund. Record it and leave it to a human --
    # apply_subscription_snapshot makes the same call for the same reason.
    if len(live) > 1:
        FORGE_BILLING_RECONCILED_TOTAL.labels(action="duplicate_detected").inc()
        logger.error(
            "billing.reconcile_duplicate: org=%s subscriptions=%s -- cancel and refund one",
            org.id,
            [s.get("id") for s in live],
        )
        await AuditService(session).log(
            action="billing.event_not_applied.duplicate_subscription",
            resource_type="subscription",
            resource_id=str(live[0].get("id")),
            org_id=org.id,
            diff={
                "reason": "duplicate_subscription",
                "detail": "reconciliation found "
                f"{len(live)} live Stripe subscriptions: "
                f"{[s.get('id') for s in live]}",
            },
            severity="elevated",
        )
        return True

    remote_sub = live[0]
    if (
        local is not None
        and local.stripe_subscription_id == remote_sub.get("id")
        and local.status == remote_sub.get("status")
    ):
        return False

    logger.warning(
        "billing.reconcile_repair: org=%s local=%s remote=%s",
        org.id,
        None if local is None else f"{local.stripe_subscription_id}/{local.status}",
        f"{remote_sub.get('id')}/{remote_sub.get('status')}",
    )
    # Stripe's own object does not carry org_id unless checkout set it; the
    # org is known here, so fill it in rather than have the shared path
    # reject the snapshot as metadata-less.
    remote_sub.setdefault("metadata", {})
    remote_sub["metadata"]["org_id"] = str(org.id)
    # `utcnow()` as the watermark, not None. This read *is* current state, so
    # every event Stripe generated before now describes something older --
    # including any that were queued while a disabled endpoint was catching
    # up. Leaving the watermark where it was would let one of those overwrite
    # the repair this sweep just made. The staleness check itself cannot
    # reject this (a watermark in the future is impossible), so passing a
    # timestamp only advances it.
    await service.apply_subscription_snapshot(remote_sub, event_created=utcnow())
    FORGE_BILLING_RECONCILED_TOTAL.labels(action="applied").inc()
    return True
