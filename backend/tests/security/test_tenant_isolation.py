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
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlmodel import SQLModel

from app.core import module_loader
from app.core.modules import ModuleConfig
from app.modules.api_keys.models import ApiKey
from app.modules.api_keys.schemas import ApiKeyCreateRequest
from app.modules.api_keys.service import ApiKeyService
from app.modules.auth.permissions import MemberRole
from app.modules.auth.repository import UserRepository
from app.modules.auth.utils import create_access_token, hash_password
from app.modules.tenancy.models import Membership, Organization
from app.modules.tenancy.repository import MembershipRepository, OrganizationRepository
from product import config as product_config

pytestmark = pytest.mark.owasp("A01")

_PASSWORD = "Correct-Horse9"


@dataclass
class TenantIsolationContext:
    client: httpx.AsyncClient
    org_a: Organization
    org_b: Organization
    user_a_headers: dict[str, str]
    org_b_owner_membership: Membership
    # T-311's sweep mints a key as this user; the session cases never need it.
    org_a_owner_id: UUID


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


@pytest.fixture(autouse=True)
async def _api_key_table(engine: AsyncEngine) -> None:
    """Create `api_key` before anything in this file writes a row.

    `public_api` ships off, so `migration_targets()` skipped its branch when
    the shared `engine` fixture ran and the table does not exist.
    `create_all` is checkfirst, so this is a no-op in a product that enables
    the module.

    **Autouse, and depending on `engine` alone, for a reason that cost an
    afternoon**: `CREATE TABLE api_key ... REFERENCES organization(id)` takes
    a SHARE ROW EXCLUSIVE lock on `organization`, which conflicts with the
    ROW EXCLUSIVE the `session` fixture's still-open outer transaction holds
    once a test has inserted an org. Created from inside `org_a_api_key`,
    which runs after `isolation_ctx` has seeded two, it blocked forever. An
    autouse fixture runs before the non-autouse ones at the same scope, so
    the table exists before the first insert and the lock is uncontended.
    """
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all, tables=[ApiKey.__table__])


@pytest.fixture
async def org_a_api_key(
    isolation_ctx: TenantIsolationContext,
    session: AsyncSession,
    redis_client: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> str:
    """A live key belonging to org_a, for the sweep below.

    The module has to be switched on as well as the table created -- the auth
    path refuses a key outright while `public_api` is off, which is
    app/core/principal.py doing its job rather than something to work around.
    """
    monkeypatch.setattr(
        product_config,
        "MODULES",
        replace(product_config.MODULES, public_api=ModuleConfig(enabled=True)),
    )
    module_loader.get_modules.cache_clear()

    owner_id = isolation_ctx.org_a_owner_id
    _, presented = await ApiKeyService(session, isolation_ctx.org_a.id, redis_client).create_key(
        owner_id,
        ApiKeyCreateRequest(name="org a", scopes=["orgs:read", "members:read"]),
    )
    await session.commit()
    return presented


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
        org_a_owner_id=owner_a.user_id,
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


# (test id, request builder, status codes that prove isolation held, owning
# module). The last field is None for the always-on tenancy surface and a
# module name for an endpoint that only exists while that module is enabled --
# T-291 uses it to keep the seven core cases running in CI's module-matrix
# combination that turns every optional module off, where the other seven
# endpoints have no route to sweep.
_CASES: list[tuple[str, RequestFn, tuple[int, ...], str | None]] = [
    ("GET /orgs/{org_id}", _get_org, (403,), None),
    ("PATCH /orgs/{org_id}", _update_org, (403,), None),
    ("GET /orgs/{org_id}/members", _list_members, (403,), None),
    ("PATCH /orgs/{org_id}/members/{membership_id}", _update_member_role, (404,), None),
    ("DELETE /orgs/{org_id}/members/{membership_id}", _remove_member, (404,), None),
    ("GET /orgs/{org_id}/invitations", _list_invitations, (403,), None),
    ("POST /orgs/{org_id}/invitations", _create_invitation, (403,), None),
    ("POST /billing/checkout-session", _checkout_session, (403,), "billing"),
    ("POST /billing/portal-session", _portal_session, (403,), "billing"),
    ("GET /billing/subscription", _get_subscription, (403,), "billing"),
    ("GET /orgs/{org_id}/feature-flags", _get_feature_flags, (403,), "feature_flags"),
    ("POST /files/presign", _presign_file_upload, (403,), "file_storage"),
    ("GET /files", _list_files, (403,), "file_storage"),
    ("GET /notifications", _list_notifications, (403,), "notifications"),
]


@pytest.mark.parametrize(
    ("make_request", "allowed_statuses"),
    [
        pytest.param(
            *case[1:3],
            marks=pytest.mark.module_dependent if case[3] is not None else (),
        )
        for case in _CASES
    ],
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


# --- the second kind of principal (T-311) ------------------------------------
#
# Everything above sweeps a *session* across the tenant boundary. T-311 added
# a second way to authenticate -- an API key -- and §0.2 has to hold for it
# identically, so the two endpoints a key can call get the same probe with the
# same shape of credential from the wrong organization.
#
# Not folded into _CASES because the parametrization there is built around one
# set of headers for one caller, and a key is a different credential per case
# rather than a different URL. The management surface under /api/v1/api-keys is
# swept in tests/modules/test_api_keys.py's first §0.8 non-negotiable, where
# the router is stood up regardless of the module toggle.


@pytest.mark.module_dependent
@pytest.mark.parametrize(
    "path",
    [
        pytest.param("", id="GET /orgs/{org_id}"),
        pytest.param("/members", id="GET /orgs/{org_id}/members"),
    ],
)
async def test_an_api_key_cannot_cross_the_tenant_boundary_either(
    path: str,
    isolation_ctx: TenantIsolationContext,
    org_a_api_key: str,
) -> None:
    """org_a's key, org_b's URL. The key is valid, unexpired, unrevoked and
    holds every scope these routes ask for -- the only thing wrong with it is
    which organization it belongs to, which is exactly the thing that must be
    enough."""
    response = await isolation_ctx.client.get(
        f"/api/v1/orgs/{isolation_ctx.org_b.id}{path}",
        headers={
            "Authorization": f"Bearer {org_a_api_key}",
            "X-Org-ID": str(isolation_ctx.org_b.id),
        },
    )

    assert response.status_code == 403
    assert response.json()["data"] is None
    assert response.json()["error"]["message_key"] == "errors.not_org_member"
