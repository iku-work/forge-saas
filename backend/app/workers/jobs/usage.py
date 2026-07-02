"""
report_usage_to_stripe (T-132): the read side of usage-based billing.
Sweeps UsageRecord rows nobody has reported yet, sums them per
(org_id, metric), and reports each sum to Stripe as a billing Meter event --
https://docs.stripe.com/billing/subscriptions/usage-based/meters, the current
API for metered billing. (The older SubscriptionItem.create_usage_record
this plan's own text describes has been removed from stripe-python; a meter
event is reported against the org's Stripe *customer*, not a subscription
item, which is simpler -- no per-subscription item lookup needed.)

Scheduled (APScheduler, not ARQ-deferred) every 15 minutes from
app.workers.scheduler, same distributed-lock pattern as every other periodic
job there.

Plan.usage_meter_event_names maps a metric name to the Stripe Meter's
event_name that bills it -- configured once per plan when the metered Price
is set up in Stripe. An org with no Stripe customer yet, no subscription, or
a plan that doesn't configure the metric is left unreported and logged --
picked up on the job's next tick once configuration catches up, not silently
dropped.

Stripe enforces `identifier` uniqueness within a rolling 24h window (see
MeterEventCreateParams), so passing one of the batch's own UsageRecord ids
makes a retried tick (session.commit() failed after Stripe accepted the
event, so the records are still unreported) report the *same* meter event
again rather than double-counting.
"""

import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Any

import stripe

from app.core.config import get_settings
from app.core.db import async_session_factory
from app.modules.billing.repository import (
    PlanRepository,
    SubscriptionRepository,
    UsageRecordRepository,
)
from app.modules.tenancy.models import Organization

if TYPE_CHECKING:
    from uuid import UUID

    from app.modules.billing.usage_models import UsageRecord

logger = logging.getLogger(__name__)


async def report_usage_to_stripe(
    ctx: dict[str, Any],  # noqa: ARG001 -- APScheduler calling convention; see app.workers.scheduler
) -> None:
    stripe.api_key = get_settings().STRIPE_SECRET_KEY
    async with async_session_factory() as session:
        usage = UsageRecordRepository(session)
        subscriptions = SubscriptionRepository(session)
        plans = PlanRepository(session)

        grouped: dict[tuple[UUID, str], list[UsageRecord]] = defaultdict(list)
        for record in await usage.list_unreported():
            grouped[(record.org_id, record.metric)].append(record)

        for (org_id, metric), records in grouped.items():
            org = await session.get(Organization, org_id)
            subscription = await subscriptions.get_by_org_id(org_id)
            if org is None or org.stripe_customer_id is None or subscription is None:
                logger.warning(
                    "report_usage_to_stripe: org=%s missing Stripe customer or "
                    "subscription, skipping metric=%s",
                    org_id,
                    metric,
                )
                continue

            plan = await plans.get_by_id(subscription.plan_id)
            event_name = plan.usage_meter_event_names.get(metric) if plan is not None else None
            if event_name is None:
                logger.warning(
                    "report_usage_to_stripe: metric=%s not configured on plan for org=%s, skipping",
                    metric,
                    org_id,
                )
                continue

            quantity = sum(record.quantity for record in records)
            meter_event = stripe.billing.MeterEvent.create(
                event_name=event_name,
                payload={"stripe_customer_id": org.stripe_customer_id, "value": str(quantity)},
                identifier=str(records[0].id),
            )
            await usage.mark_reported(records, stripe_usage_record_id=meter_event.identifier)

        await session.commit()
