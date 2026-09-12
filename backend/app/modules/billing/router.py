"""
HTTP layer for checkout/portal redirects, the org's current subscription,
and the public plan catalog (T-031). Every route here is a flat
/billing/... path with no org_id path segment, so org resolution goes
through app.core.dependencies' X-Org-ID-header convention (get_current_org +
require_permission), not app.modules.tenancy.router's path-keyed one.

create_checkout_session and create_portal_session both need the
Organization object itself (to read/persist stripe_customer_id), not just
permission confirmation -- so each declares get_current_org directly
alongside require_permission rather than only depending on the latter.
FastAPI caches get_current_org per request, so this costs no extra query.
"""

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.dependencies import (
    get_current_org,
    get_redis,
    reject_during_impersonation,
    require_permission,
    require_recent_auth,
)
from app.core.envelope import Envelope, Meta
from app.middleware.rate_limit import api_limit
from app.modules.auth.permissions import Permission
from app.modules.billing.invoices import InvoiceDTO, InvoiceService
from app.modules.billing.schemas import (
    CheckoutSessionRequest,
    CheckoutSessionResponse,
    PlanPublic,
    PortalSessionResponse,
    SubscriptionPublic,
    UsageSummary,
)
from app.modules.billing.service import BillingService
from app.modules.tenancy.models import Membership, Organization

router = APIRouter(prefix="/billing", tags=["billing"])


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid4())


# Deliberately NOT gated on require_recent_auth, though it was until
# 2026-09-10. Ask what a stolen session gains here: a Stripe-hosted URL on
# which the attacker would have to enter their own card, to add a paid plan
# to somebody else's account. No theft, no disclosure -- while the cost was
# real, because auth_time is not advanced by a silent token refresh (see
# Session.auth_time), so anyone whose tab had been open more than five
# minutes was told to sign in again to upgrade. That is a checkout
# interruption in exchange for approximately nothing.
#
# The control moved to /portal-session below rather than being deleted. That
# is where the damage a hijacked session can do actually lives.
@router.post("/checkout-session", dependencies=[Depends(reject_during_impersonation("billing"))])
@api_limit
async def create_checkout_session(  # noqa: PLR0913 — T-120 adds redis on top of the existing 5
    request: Request,
    body: CheckoutSessionRequest,
    org_and_membership: Annotated[tuple[Organization, Membership], Depends(get_current_org)],
    membership: Annotated[  # noqa: ARG001 — required to gate on BILLING_MANAGE
        Membership, Depends(require_permission(Permission.BILLING_MANAGE))
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[CheckoutSessionResponse]:
    org, _ = org_and_membership
    url = await BillingService(session, redis).create_checkout_session(
        org,
        body.plan_slug,
        body.interval,
        # So Stripe returns the customer to the locale they were browsing in,
        # rather than the product default. Set by the i18n middleware.
        locale=getattr(request.state, "locale", None),
    )
    await session.commit()
    return Envelope(
        data=CheckoutSessionResponse(url=url), meta=Meta(request_id=_request_id(request))
    )


# T-218 lives here now, not on checkout. The Stripe Customer Portal is the
# billing surface with something worth stealing behind it: the payment
# instrument on file, the invoice history, and -- when the portal is
# configured to allow it -- cancelling the subscription outright. Unlike
# starting a checkout, every one of those is damage a hijacked session can do
# on its own, so proving the session was established recently is worth an
# interruption here.
@router.post("/portal-session", dependencies=[Depends(reject_during_impersonation("billing"))])
@api_limit
async def create_portal_session(
    request: Request,
    org_and_membership: Annotated[tuple[Organization, Membership], Depends(get_current_org)],
    membership: Annotated[  # noqa: ARG001 — required to gate on BILLING_MANAGE
        Membership, Depends(require_permission(Permission.BILLING_MANAGE))
    ],
    _recent_auth: Annotated[None, Depends(require_recent_auth(max_age_minutes=5))],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[PortalSessionResponse]:
    org, _ = org_and_membership
    url = await BillingService(session, redis).create_portal_session(
        org, locale=getattr(request.state, "locale", None)
    )
    await session.commit()
    return Envelope(data=PortalSessionResponse(url=url), meta=Meta(request_id=_request_id(request)))


@router.get("/subscription")
@api_limit
async def get_subscription(
    request: Request,
    org_and_membership: Annotated[tuple[Organization, Membership], Depends(get_current_org)],
    membership: Annotated[  # noqa: ARG001 — required to gate on BILLING_READ
        Membership, Depends(require_permission(Permission.BILLING_READ))
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[SubscriptionPublic | None]:
    org, _ = org_and_membership
    subscription = await BillingService(session, redis).get_subscription(org.id)
    data = SubscriptionPublic.model_validate(subscription) if subscription is not None else None
    return Envelope(data=data, meta=Meta(request_id=_request_id(request)))


@router.get("/usage")
@api_limit
async def get_usage(
    request: Request,
    org_and_membership: Annotated[tuple[Organization, Membership], Depends(get_current_org)],
    membership: Annotated[  # noqa: ARG001 — required to gate on BILLING_READ
        Membership, Depends(require_permission(Permission.BILLING_READ))
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[list[UsageSummary]]:
    org, _ = org_and_membership
    data = await BillingService(session, redis).get_usage_summary(org.id)
    return Envelope(data=data, meta=Meta(request_id=_request_id(request)))


@router.get("/plans")
@api_limit
async def list_plans(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[list[PlanPublic]]:
    plans = await BillingService(session, redis).list_active_plans()
    data = [PlanPublic.model_validate(p) for p in plans]
    return Envelope(data=data, meta=Meta(request_id=_request_id(request)))


@router.get("/invoices")
@api_limit
async def list_invoices(  # noqa: PLR0913 — cursor pagination (§0.3) needs after+limit on top of the usual params
    request: Request,
    org_and_membership: Annotated[tuple[Organization, Membership], Depends(get_current_org)],
    membership: Annotated[  # noqa: ARG001 — required to gate on BILLING_READ
        Membership, Depends(require_permission(Permission.BILLING_READ))
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
    after: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Envelope[list[InvoiceDTO]]:
    org, _ = org_and_membership
    items, next_cursor = await InvoiceService(session, redis).list_for_org(
        org, after_cursor=after, limit=limit
    )
    return Envelope(data=items, meta=Meta(request_id=_request_id(request), next_cursor=next_cursor))
