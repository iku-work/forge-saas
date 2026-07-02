"""
DB access for the billing module (T-030): Plan lookups, Subscription
upserts, and the WebhookEvent idempotency ledger. T-031 adds the read paths
app.modules.billing.dependencies and .service need: Plan.get_by_id/get_by_slug/
list_active and Subscription.get_by_org_id.

None of the three satisfy BaseRepository's `_OrgScopedRecord` shape: Plan is
a catalog table with no org_id at all, Subscription has no `deleted_at`
(cancellation is a status change driven by Stripe, never a local soft
delete), and WebhookEvent is keyed by Stripe's own event id and isn't
org-scoped -- a single webhook delivery can affect any org. Bespoke classes,
same pattern as app.modules.tenancy.repository.
"""

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models._mixins import utcnow
from app.modules.billing.models import Plan, Subscription, WebhookEvent


class PlanRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_stripe_price_id(self, price_id: str) -> Plan | None:
        # A plan's monthly and yearly Stripe prices both resolve to the same
        # local Plan row -- a subscription event only ever carries one of
        # the two, so either column matching is sufficient.
        m = cast("Any", Plan)
        stmt = select(Plan).where(
            or_(m.stripe_price_id_monthly == price_id, m.stripe_price_id_yearly == price_id),
            m.deleted_at.is_(None),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, plan_id: UUID) -> Plan | None:
        return await self.session.get(Plan, plan_id)

    async def get_by_slug(self, slug: str) -> Plan | None:
        m = cast("Any", Plan)
        stmt = select(Plan).where(m.slug == slug, m.deleted_at.is_(None), m.is_active.is_(True))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_active(self) -> list[Plan]:
        m = cast("Any", Plan)
        stmt = (
            select(Plan)
            .where(m.deleted_at.is_(None), m.is_active.is_(True))
            .order_by(m.price_monthly.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class SubscriptionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_stripe_subscription_id(
        self, stripe_subscription_id: str
    ) -> Subscription | None:
        m = cast("Any", Subscription)
        stmt = select(Subscription).where(m.stripe_subscription_id == stripe_subscription_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_org_id(self, org_id: UUID) -> Subscription | None:
        # org_id is unique on this table (§"Billing models", T-029) -- one
        # subscription per org -- so this is a lookup, not a list.
        m = cast("Any", Subscription)
        stmt = select(Subscription).where(m.org_id == org_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def count_trialing_expiring_within(self, horizon: datetime) -> int:
        # Used by app.workers.scheduler.sync_expiring_trials (T-034) for a
        # heartbeat count of trials ending soon. The actual warning-email
        # logic (with idempotent per-subscription send tracking) is T-131's
        # notify_expiring_trials -- this job is observability only.
        m = cast("Any", Subscription)
        stmt = (
            select(func.count())
            .select_from(Subscription)
            .where(m.status == "trialing", m.trial_end.is_not(None), m.trial_end <= horizon)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def upsert(  # noqa: PLR0913 -- mirrors Stripe's subscription object fields 1:1
        self,
        *,
        org_id: UUID,
        plan_id: UUID,
        stripe_subscription_id: str,
        status: str,
        current_period_start: datetime,
        current_period_end: datetime,
        cancel_at_period_end: bool,
        trial_end: datetime | None,
    ) -> Subscription:
        existing = await self.get_by_stripe_subscription_id(stripe_subscription_id)
        if existing is not None:
            existing.org_id = org_id
            existing.plan_id = plan_id
            existing.status = status
            existing.current_period_start = current_period_start
            existing.current_period_end = current_period_end
            existing.cancel_at_period_end = cancel_at_period_end
            existing.trial_end = trial_end
            await self.session.flush()
            return existing

        subscription = Subscription(
            org_id=org_id,
            plan_id=plan_id,
            stripe_subscription_id=stripe_subscription_id,
            status=status,
            current_period_start=current_period_start,
            current_period_end=current_period_end,
            cancel_at_period_end=cancel_at_period_end,
            trial_end=trial_end,
        )
        self.session.add(subscription)
        await self.session.flush()
        return subscription

    async def set_status_by_stripe_subscription_id(
        self, stripe_subscription_id: str, status: str
    ) -> Subscription | None:
        subscription = await self.get_by_stripe_subscription_id(stripe_subscription_id)
        if subscription is None:
            return None
        subscription.status = status
        await self.session.flush()
        return subscription


class WebhookEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, event_id: str) -> WebhookEvent | None:
        return await self.session.get(WebhookEvent, event_id)

    async def create(
        self, *, event_id: str, event_type: str, payload: dict[str, Any]
    ) -> WebhookEvent:
        event = WebhookEvent(id=event_id, type=event_type, payload=payload)
        self.session.add(event)
        await self.session.flush()
        return event

    async def mark_processed(self, event: WebhookEvent) -> None:
        event.processed_at = utcnow()
        await self.session.flush()

    async def mark_failed(self, event: WebhookEvent, *, error: str) -> None:
        event.error = error
        event.attempt_count += 1
        await self.session.flush()
