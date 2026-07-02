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

The day-1 email needs the failed invoice's amount, which DunningCampaign
doesn't store (only invoice_id) -- fetched from Stripe at send time instead
of threaded through from the webhook payload, since a background job talking
to a slow third party is exactly the case app.modules.billing.service's own
docstring calls out for *not* doing synchronously in the request path.
"""

import logging
from typing import Any, cast
from uuid import UUID

import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import jobs
from app.core.config import get_settings
from app.core.db import async_session_factory
from app.modules.audit.service import AuditService
from app.modules.auth.models import User
from app.modules.auth.permissions import MemberRole
from app.modules.billing.repository import (
    DunningCampaignRepository,
    PlanRepository,
    SubscriptionRepository,
)
from app.modules.tenancy.models import Membership, Organization

logger = logging.getLogger(__name__)

_TEMPLATE_BY_STEP = {
    "day1": "payment_failed",
    "day3": "subscription_expiring",
    "day7": "subscription_suspended",
}

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


async def send_dunning_email(
    ctx: dict[str, Any],  # noqa: ARG001 -- ARQ calling convention; registered in app.workers.main
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
        billing_url = f"https://{get_settings().DOMAIN}/settings/billing"

        context_extra: dict[str, Any]
        if step == "day1":
            stripe.api_key = get_settings().STRIPE_SECRET_KEY
            invoice = stripe.Invoice.retrieve(campaign.invoice_id)
            context_extra = {"amount": _format_amount(invoice.amount_due, invoice.currency)}
        elif step == "day3":
            context_extra = {
                "plan_name": plan.name,
                "expires_at": subscription.current_period_end.date().isoformat(),
            }
        else:
            context_extra = {"plan_name": plan.name}

        for owner in owners:
            await jobs.enqueue_job(
                "send_email",
                to=owner.email,
                template=_TEMPLATE_BY_STEP[step],
                locale=owner.locale,
                context={
                    "user_name": owner.full_name,
                    "org_name": org.name,
                    "billing_url": billing_url,
                    **context_extra,
                },
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
