"""
T-049: a single parametrized sweep across every org-scoped endpoint, proving
a member of org_a can never reach org_b's resources -- the IDOR class of bug
§0.2 ("org_id is sacred") and OWASP A01 are both about. This complements,
rather than duplicates, the per-endpoint cross-tenant tests already in
test_tenancy_router.py and test_billing_router.py: those cover business-rule
detail (which permission, which error code); this file is the one place a
future task must add a case to when it ships a new org-scoped endpoint (see
T-049's "Done when" cross-references from T-112 and T-212).

Reuses the root conftest.py's real-Postgres/real-Redis `client` + `session`
fixtures rather than rolling another module-scoped testcontainers setup --
nothing here needs the bespoke table subsets the router test files build.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.permissions import MemberRole
from app.modules.auth.repository import UserRepository
from app.modules.auth.utils import create_access_token, hash_password
from app.modules.tenancy.models import Membership, Organization
from app.modules.tenancy.repository import MembershipRepository, OrganizationRepository

pytestmark = pytest.mark.owasp("A01")

_PASSWORD = "Correct-Horse9"


@dataclass
class TenantIsolationContext:
    client: httpx.AsyncClient
    org_a: Organization
    org_b: Organization
    user_a_headers: dict[str, str]
    org_b_owner_membership: Membership


RequestFn = Callable[[TenantIsolationContext], Awaitable[httpx.Response]]


async def _create_org_with_owner(
    session: AsyncSession, *, name: str
) -> tuple[Organization, Membership]:
    user = await UserRepository(session).create(
        email=f"{name.lower()}-{uuid4()}@example.com",
        hashed_password=hash_password(_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    org = await OrganizationRepository(session).create(name=name, slug=f"{name.lower()}-{uuid4()}")
    membership = await MembershipRepository(session).create(
        user_id=user.id, org_id=org.id, role=MemberRole.OWNER
    )
    return org, membership


@pytest.fixture
async def isolation_ctx(client: httpx.AsyncClient, session: AsyncSession) -> TenantIsolationContext:
    org_a, owner_a = await _create_org_with_owner(session, name="OrgA")
    org_b, owner_b = await _create_org_with_owner(session, name="OrgB")
    await session.commit()

    token = create_access_token(owner_a.user_id, None, ())
    return TenantIsolationContext(
        client=client,
        org_a=org_a,
        org_b=org_b,
        user_a_headers={"Authorization": f"Bearer {token}"},
        org_b_owner_membership=owner_b,
    )


# --- one request-builder per org-scoped endpoint, each addressing org_b's
# resource as user_a -------------------------------------------------------


async def _get_org(ctx: TenantIsolationContext) -> httpx.Response:
    return await ctx.client.get(f"/api/v1/orgs/{ctx.org_b.id}", headers=ctx.user_a_headers)


async def _update_org(ctx: TenantIsolationContext) -> httpx.Response:
    return await ctx.client.patch(
        f"/api/v1/orgs/{ctx.org_b.id}", json={"name": "Hijacked"}, headers=ctx.user_a_headers
    )


async def _list_members(ctx: TenantIsolationContext) -> httpx.Response:
    return await ctx.client.get(f"/api/v1/orgs/{ctx.org_b.id}/members", headers=ctx.user_a_headers)


async def _update_member_role(ctx: TenantIsolationContext) -> httpx.Response:
    # org_id in the path is user_a's *own* org (so the permission gate
    # passes) -- the cross-tenant vector here is membership_id pointing at a
    # different org's row, the case service.py's change_role guards against.
    return await ctx.client.patch(
        f"/api/v1/orgs/{ctx.org_a.id}/members/{ctx.org_b_owner_membership.id}",
        json={"role": "admin"},
        headers=ctx.user_a_headers,
    )


async def _remove_member(ctx: TenantIsolationContext) -> httpx.Response:
    return await ctx.client.delete(
        f"/api/v1/orgs/{ctx.org_a.id}/members/{ctx.org_b_owner_membership.id}",
        headers=ctx.user_a_headers,
    )


async def _list_invitations(ctx: TenantIsolationContext) -> httpx.Response:
    return await ctx.client.get(
        f"/api/v1/orgs/{ctx.org_b.id}/invitations", headers=ctx.user_a_headers
    )


async def _create_invitation(ctx: TenantIsolationContext) -> httpx.Response:
    return await ctx.client.post(
        f"/api/v1/orgs/{ctx.org_b.id}/invitations",
        json={"email": "intruder@example.com", "role": "member"},
        headers=ctx.user_a_headers,
    )


async def _checkout_session(ctx: TenantIsolationContext) -> httpx.Response:
    return await ctx.client.post(
        "/api/v1/billing/checkout-session",
        json={"plan_slug": "pro", "interval": "monthly"},
        headers={**ctx.user_a_headers, "X-Org-ID": str(ctx.org_b.id)},
    )


async def _portal_session(ctx: TenantIsolationContext) -> httpx.Response:
    return await ctx.client.post(
        "/api/v1/billing/portal-session",
        headers={**ctx.user_a_headers, "X-Org-ID": str(ctx.org_b.id)},
    )


async def _get_subscription(ctx: TenantIsolationContext) -> httpx.Response:
    return await ctx.client.get(
        "/api/v1/billing/subscription",
        headers={**ctx.user_a_headers, "X-Org-ID": str(ctx.org_b.id)},
    )


async def _get_feature_flags(ctx: TenantIsolationContext) -> httpx.Response:
    return await ctx.client.get(
        f"/api/v1/orgs/{ctx.org_b.id}/feature-flags", headers=ctx.user_a_headers
    )


async def _presign_file_upload(ctx: TenantIsolationContext) -> httpx.Response:
    return await ctx.client.post(
        "/api/v1/files/presign",
        json={"filename": "x.txt", "content_type": "text/plain", "size_bytes": 1},
        headers={**ctx.user_a_headers, "X-Org-ID": str(ctx.org_b.id)},
    )


async def _list_files(ctx: TenantIsolationContext) -> httpx.Response:
    return await ctx.client.get(
        "/api/v1/files", headers={**ctx.user_a_headers, "X-Org-ID": str(ctx.org_b.id)}
    )


async def _list_notifications(ctx: TenantIsolationContext) -> httpx.Response:
    return await ctx.client.get(
        "/api/v1/notifications", headers={**ctx.user_a_headers, "X-Org-ID": str(ctx.org_b.id)}
    )


# (test id, request builder, status codes that prove isolation held)
_CASES: list[tuple[str, RequestFn, tuple[int, ...]]] = [
    ("GET /orgs/{org_id}", _get_org, (403,)),
    ("PATCH /orgs/{org_id}", _update_org, (403,)),
    ("GET /orgs/{org_id}/members", _list_members, (403,)),
    ("PATCH /orgs/{org_id}/members/{membership_id}", _update_member_role, (404,)),
    ("DELETE /orgs/{org_id}/members/{membership_id}", _remove_member, (404,)),
    ("GET /orgs/{org_id}/invitations", _list_invitations, (403,)),
    ("POST /orgs/{org_id}/invitations", _create_invitation, (403,)),
    ("POST /billing/checkout-session", _checkout_session, (403,)),
    ("POST /billing/portal-session", _portal_session, (403,)),
    ("GET /billing/subscription", _get_subscription, (403,)),
    ("GET /orgs/{org_id}/feature-flags", _get_feature_flags, (403,)),
    ("POST /files/presign", _presign_file_upload, (403,)),
    ("GET /files", _list_files, (403,)),
    ("GET /notifications", _list_notifications, (403,)),
]


@pytest.mark.parametrize(
    ("make_request", "allowed_statuses"),
    [case[1:] for case in _CASES],
    ids=[case[0] for case in _CASES],
)
async def test_cross_tenant_access_denied(
    make_request: RequestFn,
    allowed_statuses: tuple[int, ...],
    isolation_ctx: TenantIsolationContext,
) -> None:
    response = await make_request(isolation_ctx)

    assert response.status_code != 200
    assert response.status_code in allowed_statuses
    assert response.json()["data"] is None
