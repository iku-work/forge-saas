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
from typing import Any, cast
from uuid import UUID

import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import jobs
from app.core.config import get_settings
from app.core.db import async_session_factory
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
from app.modules.notifications.service import NotificationService
from app.modules.tenancy.models import Membership, Organization

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
    ctx: dict[str, Any],  # noqa: ARG001 -- APScheduler calling convention; see app.workers.scheduler
) -> None:
    now = utcnow()
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

        await session.commit()
