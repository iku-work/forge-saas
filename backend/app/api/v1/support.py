"""
T-262: the signed-in user's support context, for surfaces that can't run a
JavaScript support widget -- the mobile app above all, which typically opens
a `mailto:` (or a vendor chat SDK) pre-populated with these fields so the
first reply doesn't have to start with "which account are you on?".

Deliberately tool-agnostic and read-only: the template never talks to
Intercom/Crisp/Plain itself, it only reports who is asking. Which tool the
product uses (and the fallback URL) is deploy-time config and rides on the
public GET /config response instead -- see app.core.support.

Same "a router may reach straight into repositories" reasoning as
app.modules.billing.dependencies (see its docstring): this is a flat
read-model over user + org + subscription with no behaviour of its own, so a
service layer would be a pass-through. Every field it can return is already
readable by this caller through /auth/me, /orgs and /billing/subscription --
it exists to make one request of what would otherwise be three.
"""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_context import get_impersonator_id
from app.core.db import get_session
from app.core.dependencies import get_current_org, get_current_user
from app.core.envelope import Envelope, Meta
from app.core.module_loader import is_enabled
from app.middleware.rate_limit import api_limit
from app.modules.auth.models import User
from app.modules.billing.repository import PlanRepository, SubscriptionRepository
from app.modules.tenancy.models import Membership, Organization

router = APIRouter(prefix="/me", tags=["support"])


class SupportContextResponse(BaseModel):
    user_id: str
    email: str
    full_name: str | None
    org_id: str
    org_name: str
    # The org's current subscription, or (None, None) when it has none --
    # a free-tier org, or a billing-disabled product where the subscription
    # table doesn't exist at all (T-062).
    plan: str | None
    subscription_status: str | None
    signup_at: str
    locale: str
    # T-142/T-260: true when a superadmin is acting as this user. Support
    # widgets must suppress their identity call in that case -- otherwise
    # the operator's own poking around lands in the real user's conversation
    # history, and any reply goes to a "user" who never asked anything.
    is_impersonating: bool


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid4())


async def _resolve_plan(session: AsyncSession, org_id: UUID) -> tuple[str | None, str | None]:
    # Guarded rather than joined unconditionally for the same reason
    # AdminOrgRepository.list_orgs guards its billing join: with the module
    # off, the subscription/plan tables don't exist at all (T-062).
    if not is_enabled("billing"):
        return None, None
    subscription = await SubscriptionRepository(session).get_by_org_id(org_id)
    if subscription is None:
        return None, None
    plan = await PlanRepository(session).get_by_id(subscription.plan_id)
    return (plan.slug if plan is not None else None), subscription.status


@router.get("/support-context")
@api_limit
async def get_support_context(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    org_and_membership: Annotated[tuple[Organization, Membership], Depends(get_current_org)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[SupportContextResponse]:
    # Cross-tenant safety is get_current_org's: it resolves the org named by
    # X-Org-ID *and* the caller's membership in it, and raises
    # PermissionDeniedError when there isn't one -- so a user in org A can
    # never be handed org B's name, plan or status here.
    org, _ = org_and_membership
    plan_slug, subscription_status = await _resolve_plan(session, org.id)
    data = SupportContextResponse(
        user_id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        org_id=str(org.id),
        org_name=org.name,
        plan=plan_slug,
        subscription_status=subscription_status,
        signup_at=user.created_at.isoformat(),
        locale=user.locale,
        is_impersonating=get_impersonator_id() is not None,
    )
    return Envelope(data=data, meta=Meta(request_id=_request_id(request)))
