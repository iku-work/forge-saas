"""
Plan-gating dependency (T-031): require_feature gates a route on the
organization's *currently entitled* Plan. Entitlement is resolved from the
org's Subscription row (kept in sync by Stripe webhooks -- see
app.modules.billing.service.handle_event), not Organization.plan_id, which
nothing in this codebase writes. An org with no Subscription row, or one
whose Subscription has lapsed past the dunning grace period, resolves to
_FREE_PLAN: an in-memory Plan with no entitlements, never persisted -- a
literal "free" catalog row would need a real Stripe price for no reason,
since nothing about the free tier is ever billed.

Same FastAPI-dependency-not-service-layer reasoning as app.core.dependencies
(see that module's docstring): this goes straight to the repository layer
rather than through BillingService, and uses the X-Org-ID-header convention
(get_current_org) rather than app.modules.tenancy.router's path-keyed one,
since every route this gates is a flat /billing/... endpoint with no org_id
segment.

T-120: require_feature's boolean gate now delegates to FeatureFlagService
(better caching, and it lets an admin override -- T-122 -- turn a feature
off per-org without touching the org's Plan). It still resolves the org's
OrgSubscription itself for the numeric `minimum` comparison, since
FeatureFlagService only ever answers a bool, not a raw plan-feature value.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, cast

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.dependencies import get_current_org, get_redis
from app.core.exceptions import PlanLimitError
from app.core.module_loader import is_enabled
from app.models._mixins import utcnow
from app.modules.billing.models import Plan, Subscription
from app.modules.billing.repository import PlanRepository, SubscriptionRepository
from app.modules.feature_flags.service import FeatureFlagService
from app.modules.tenancy.models import Membership, Organization

_GRACE_PERIOD = timedelta(days=7)
_ENTITLED_STATUSES = frozenset({"active", "trialing"})

_FREE_PLAN = Plan(
    name="Free",
    slug="free",
    stripe_price_id_monthly="",
    stripe_price_id_yearly="",
    price_monthly=0,
    price_yearly=0,
    features={},
)


@dataclass
class OrgSubscription:
    org: Organization
    subscription: Subscription | None
    plan: Plan


def _is_entitled(subscription: Subscription) -> bool:
    if subscription.status in _ENTITLED_STATUSES:
        return True
    if subscription.status == "past_due":
        return utcnow() <= subscription.current_period_end + _GRACE_PERIOD
    return False


async def resolve_org_subscription(org: Organization, session: AsyncSession) -> OrgSubscription:
    subscription = await SubscriptionRepository(session).get_by_org_id(org.id)
    if subscription is None or not _is_entitled(subscription):
        return OrgSubscription(org=org, subscription=subscription, plan=_FREE_PLAN)

    plan = await PlanRepository(session).get_by_id(subscription.plan_id)
    # subscription.plan_id is a NOT NULL FK to plan.id (T-029) -- an entitled
    # subscription's plan row is always present.
    return OrgSubscription(org=org, subscription=subscription, plan=cast("Plan", plan))


async def get_current_org_subscription(
    org_and_membership: Annotated[tuple[Organization, Membership], Depends(get_current_org)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OrgSubscription:
    org, _ = org_and_membership
    return await resolve_org_subscription(org, session)


def require_feature(
    feature: str, minimum: int | None = None
) -> Callable[..., Awaitable[OrgSubscription | None]]:
    async def checker(
        org_and_membership: Annotated[tuple[Organization, Membership], Depends(get_current_org)],
        session: Annotated[AsyncSession, Depends(get_session)],
        redis: Annotated[Redis, Depends(get_redis)],
    ) -> OrgSubscription | None:
        # Billing disabled -> every feature is implicitly available, so
        # non-billing products work with no Plan/Subscription tables at all
        # (T-065). Must short-circuit here, before get_current_org_subscription
        # runs -- those tables don't exist when billing is disabled (T-062).
        if not is_enabled("billing"):
            return None

        org_sub = await get_current_org_subscription(org_and_membership, session)
        flag_enabled = await FeatureFlagService(session, redis).is_enabled(org_sub.org, feature)
        if not flag_enabled:
            raise PlanLimitError(
                "errors.feature_unavailable",
                fallback=f"Feature '{feature}' not available on your plan",
                params={"feature": feature},
            )
        value = org_sub.plan.features.get(feature)
        if minimum is not None and (value is None or value < minimum):
            raise PlanLimitError(
                "errors.plan_limit_reached_for_feature",
                fallback=f"Plan limit reached for '{feature}'",
                params={"feature": feature},
            )
        return org_sub

    return checker
