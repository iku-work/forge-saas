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

from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.dependencies import get_current_org, get_redis, require_permission
from app.core.envelope import Envelope, Meta
from app.middleware.rate_limit import api_limit
from app.modules.auth.permissions import Permission
from app.modules.billing.schemas import (
    CheckoutSessionRequest,
    CheckoutSessionResponse,
    PlanPublic,
    PortalSessionResponse,
    SubscriptionPublic,
)
from app.modules.billing.service import BillingService
from app.modules.tenancy.models import Membership, Organization

router = APIRouter(prefix="/billing", tags=["billing"])


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid4())


@router.post("/checkout-session")
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
        org, body.plan_slug, body.interval
    )
    await session.commit()
    return Envelope(
        data=CheckoutSessionResponse(url=url), meta=Meta(request_id=_request_id(request))
    )


@router.post("/portal-session")
@api_limit
async def create_portal_session(
    request: Request,
    org_and_membership: Annotated[tuple[Organization, Membership], Depends(get_current_org)],
    membership: Annotated[  # noqa: ARG001 — required to gate on BILLING_MANAGE
        Membership, Depends(require_permission(Permission.BILLING_MANAGE))
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[PortalSessionResponse]:
    org, _ = org_and_membership
    url = await BillingService(session, redis).create_portal_session(org)
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
