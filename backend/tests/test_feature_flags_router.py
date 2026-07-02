"""
Verifies T-120's read-only GET /orgs/{org_id}/feature-flags endpoint:
membership is resolved from the *path* org id (mirrors
test_tenancy_router.py's convention, not the X-Org-ID header), a non-member
gets 403, and a member sees the FeatureFlagService-resolved map for their
org's real Plan.

Uses conftest's shared client/session/_redis_client fixtures (full
migrations) rather than a throwaway engine.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.permissions import MemberRole
from app.modules.auth.repository import UserRepository
from app.modules.auth.utils import create_access_token, hash_password
from app.modules.billing.models import Plan, Subscription
from app.modules.tenancy.models import Organization
from app.modules.tenancy.repository import MembershipRepository, OrganizationRepository

_PASSWORD = "Correct-Horse9"


async def _member(
    client: httpx.AsyncClient, session: AsyncSession, org: Organization, role: MemberRole
) -> httpx.AsyncClient:
    user = await UserRepository(session).create(
        email=f"user-{uuid4()}@example.com",
        hashed_password=hash_password(_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    await MembershipRepository(session).create(user_id=user.id, org_id=org.id, role=role)
    await session.commit()
    token = create_access_token(user.id, None, ())
    client.headers["Authorization"] = f"Bearer {token}"
    return client


async def test_returns_resolved_flags_for_a_member(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    org = await OrganizationRepository(session).create(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    plan = Plan(
        name="Pro",
        slug=f"pro-{uuid4().hex[:8]}",
        stripe_price_id_monthly="price_m",
        stripe_price_id_yearly="price_y",
        price_monthly=2900,
        price_yearly=29000,
        features={"advanced_reports": 1},
    )
    session.add(plan)
    await session.flush()
    session.add(
        Subscription(
            org_id=org.id,
            plan_id=plan.id,
            stripe_subscription_id=f"sub_{uuid4().hex[:8]}",
            status="active",
            current_period_start=datetime.now(UTC) - timedelta(days=10),
            current_period_end=datetime.now(UTC) + timedelta(days=20),
        )
    )
    await session.commit()
    member_client = await _member(client, session, org, MemberRole.OWNER)

    response = await member_client.get(f"/api/v1/orgs/{org.id}/feature-flags")

    assert response.status_code == 200
    assert response.json()["data"]["flags"] == {"advanced_reports": True}


async def test_non_member_gets_403(client: httpx.AsyncClient, session: AsyncSession) -> None:
    org = await OrganizationRepository(session).create(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    other_org = await OrganizationRepository(session).create(
        name="Beta", slug=f"beta-{uuid4().hex[:8]}"
    )
    member_client = await _member(client, session, other_org, MemberRole.OWNER)

    response = await member_client.get(f"/api/v1/orgs/{org.id}/feature-flags")

    assert response.status_code == 403


async def test_requires_authentication(client: httpx.AsyncClient, session: AsyncSession) -> None:
    org = await OrganizationRepository(session).create(name="Acme", slug=f"acme-{uuid4().hex[:8]}")

    response = await client.get(f"/api/v1/orgs/{org.id}/feature-flags")

    assert response.status_code == 401
