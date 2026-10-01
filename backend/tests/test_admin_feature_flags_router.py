"""
Verifies T-122's admin feature-flag override endpoints under /api/v1/admin/:
non-superadmin gets 403, a superadmin's PUT/DELETE narrows/reverts the
resolved flag (immediately, via T-120's own cache-invalidating
set_override/delete_override), and every mutation writes an AuditEvent
(action="admin.feature_flag.set").

Uses conftest's shared client/session/redis_client fixtures (full
migrations), same convention as test_feature_flags_router.py.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.repository import AuditRepository
from app.modules.auth.permissions import MemberRole
from app.modules.auth.repository import UserRepository
from app.modules.auth.utils import create_access_token, hash_password
from app.modules.billing.models import Plan, Subscription
from app.modules.tenancy.models import Organization
from app.modules.tenancy.repository import MembershipRepository, OrganizationRepository
from tests._auth import session_token

# T-291: this file needs admin + feature_flags enabled -- its routes, its tables, or
# both. CI's module-matrix job deselects it in the combination that turns
# every optional module off.
pytestmark = pytest.mark.module_dependent

_PASSWORD = "Correct-Horse9"
_FLAG = "advanced_reports"


async def _org_with_plan_flag(session: AsyncSession) -> Organization:
    org = await OrganizationRepository(session).create(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    plan = Plan(
        name="Pro",
        slug=f"pro-{uuid4().hex[:8]}",
        stripe_price_id_monthly="price_m",
        stripe_price_id_yearly="price_y",
        price_monthly=2900,
        price_yearly=29000,
        features={_FLAG: 1},
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
    return org


async def _as_superadmin(client: httpx.AsyncClient, session: AsyncSession) -> httpx.AsyncClient:
    # T-218: require_superadmin now also gates on 2FA being enrolled.
    user = await UserRepository(session).create(
        email=f"admin-{uuid4()}@example.com",
        hashed_password=hash_password(_PASSWORD),
        email_verified_at=datetime.now(UTC),
        is_superadmin=True,
        totp_enabled=True,
    )
    await session.commit()
    token = await session_token(session, user)
    client.headers["Authorization"] = f"Bearer {token}"
    return client


async def _as_non_superadmin(client: httpx.AsyncClient, session: AsyncSession) -> httpx.AsyncClient:
    user = await UserRepository(session).create(
        email=f"user-{uuid4()}@example.com",
        hashed_password=hash_password(_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    await session.commit()
    token = create_access_token(user.id, None, ())
    client.headers["Authorization"] = f"Bearer {token}"
    return client


# ---- 403 for non-superadmin -------------------------------------------------


async def test_get_denies_non_superadmin(client: httpx.AsyncClient, session: AsyncSession) -> None:
    org = await _org_with_plan_flag(session)
    admin_client = await _as_non_superadmin(client, session)

    response = await admin_client.get(f"/api/v1/admin/orgs/{org.id}/feature-flags")

    assert response.status_code == 403


async def test_put_denies_non_superadmin(client: httpx.AsyncClient, session: AsyncSession) -> None:
    org = await _org_with_plan_flag(session)
    admin_client = await _as_non_superadmin(client, session)

    response = await admin_client.put(
        f"/api/v1/admin/orgs/{org.id}/feature-flags/{_FLAG}", json={"enabled": False}
    )

    assert response.status_code == 403


async def test_delete_denies_non_superadmin(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    org = await _org_with_plan_flag(session)
    admin_client = await _as_non_superadmin(client, session)

    response = await admin_client.delete(f"/api/v1/admin/orgs/{org.id}/feature-flags/{_FLAG}")

    assert response.status_code == 403


async def test_requires_authentication(client: httpx.AsyncClient, session: AsyncSession) -> None:
    org = await _org_with_plan_flag(session)

    response = await client.get(f"/api/v1/admin/orgs/{org.id}/feature-flags")

    assert response.status_code == 401


# ---- resolved map + override state ------------------------------------------


async def test_get_returns_resolved_map_with_no_override(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    org = await _org_with_plan_flag(session)
    admin_client = await _as_superadmin(client, session)

    response = await admin_client.get(f"/api/v1/admin/orgs/{org.id}/feature-flags")

    assert response.status_code == 200
    flags = response.json()["data"]["flags"]
    assert flags[_FLAG] == {"resolved": True, "override": None}


async def test_get_returns_404_for_nonexistent_org(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin_client = await _as_superadmin(client, session)

    response = await admin_client.get(f"/api/v1/admin/orgs/{uuid4()}/feature-flags")

    assert response.status_code == 404


# ---- set override (PUT) -----------------------------------------------------


async def test_set_override_narrows_resolved_flag_immediately(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    org = await _org_with_plan_flag(session)
    admin_client = await _as_superadmin(client, session)

    put_response = await admin_client.put(
        f"/api/v1/admin/orgs/{org.id}/feature-flags/{_FLAG}", json={"enabled": False}
    )
    assert put_response.status_code == 200

    get_response = await admin_client.get(f"/api/v1/admin/orgs/{org.id}/feature-flags")
    flags = get_response.json()["data"]["flags"]
    assert flags[_FLAG] == {"resolved": False, "override": False}


async def test_set_override_reflected_on_member_endpoint_immediately(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    # Two distinct actors share one httpx client (conftest's convention, see
    # test_tenant_isolation.py) -- headers are passed explicitly per request
    # rather than mutated on client.headers, since the two logins would
    # otherwise clobber each other's Authorization header.
    org = await _org_with_plan_flag(session)
    member = await UserRepository(session).create(
        email=f"member-{uuid4()}@example.com",
        hashed_password=hash_password(_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    await MembershipRepository(session).create(
        user_id=member.id, org_id=org.id, role=MemberRole.OWNER
    )
    admin = await UserRepository(session).create(
        email=f"admin-{uuid4()}@example.com",
        hashed_password=hash_password(_PASSWORD),
        email_verified_at=datetime.now(UTC),
        is_superadmin=True,
        totp_enabled=True,
    )
    await session.commit()
    member_headers = {"Authorization": f"Bearer {create_access_token(member.id, None, ())}"}
    admin_headers = {"Authorization": f"Bearer {await session_token(session, admin)}"}

    # Warm the member-facing cache before the override is set, proving the
    # PUT's cache invalidation (T-120) -- not just a cold read -- is what
    # makes the new value visible.
    warm = await client.get(f"/api/v1/orgs/{org.id}/feature-flags", headers=member_headers)
    assert warm.json()["data"]["flags"][_FLAG] is True

    put_response = await client.put(
        f"/api/v1/admin/orgs/{org.id}/feature-flags/{_FLAG}",
        json={"enabled": False},
        headers=admin_headers,
    )
    assert put_response.status_code == 200

    member_response = await client.get(
        f"/api/v1/orgs/{org.id}/feature-flags", headers=member_headers
    )
    assert member_response.json()["data"]["flags"][_FLAG] is False


async def test_set_override_cannot_grant_a_flag_the_plan_denies(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    org = await _org_with_plan_flag(session)
    admin_client = await _as_superadmin(client, session)

    put_response = await admin_client.put(
        f"/api/v1/admin/orgs/{org.id}/feature-flags/not_on_plan", json={"enabled": True}
    )
    assert put_response.status_code == 200

    get_response = await admin_client.get(f"/api/v1/admin/orgs/{org.id}/feature-flags")
    flags = get_response.json()["data"]["flags"]
    assert flags["not_on_plan"] == {"resolved": False, "override": True}


async def test_set_override_writes_audit_event(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    org = await _org_with_plan_flag(session)
    admin_client = await _as_superadmin(client, session)

    response = await admin_client.put(
        f"/api/v1/admin/orgs/{org.id}/feature-flags/{_FLAG}", json={"enabled": False}
    )
    assert response.status_code == 200

    events, _ = await AuditRepository(session).list_cursor(org.id)
    matching = [e for e in events if e.action == "admin.feature_flag.set"]
    assert len(matching) == 1
    assert matching[0].resource_type == "feature_flag"
    assert matching[0].resource_id == _FLAG
    assert matching[0].diff == {"flag": _FLAG, "enabled": False}
    assert matching[0].org_id == org.id


async def test_put_returns_404_for_nonexistent_org(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin_client = await _as_superadmin(client, session)

    response = await admin_client.put(
        f"/api/v1/admin/orgs/{uuid4()}/feature-flags/{_FLAG}", json={"enabled": False}
    )

    assert response.status_code == 404


# ---- delete override (DELETE) -----------------------------------------------


async def test_delete_override_reverts_to_plan_default(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    org = await _org_with_plan_flag(session)
    admin_client = await _as_superadmin(client, session)
    await admin_client.put(
        f"/api/v1/admin/orgs/{org.id}/feature-flags/{_FLAG}", json={"enabled": False}
    )

    delete_response = await admin_client.delete(
        f"/api/v1/admin/orgs/{org.id}/feature-flags/{_FLAG}"
    )
    assert delete_response.status_code == 200

    get_response = await admin_client.get(f"/api/v1/admin/orgs/{org.id}/feature-flags")
    flags = get_response.json()["data"]["flags"]
    assert flags[_FLAG] == {"resolved": True, "override": None}


async def test_delete_override_writes_audit_event_with_null_value(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    org = await _org_with_plan_flag(session)
    admin_client = await _as_superadmin(client, session)
    await admin_client.put(
        f"/api/v1/admin/orgs/{org.id}/feature-flags/{_FLAG}", json={"enabled": False}
    )

    response = await admin_client.delete(f"/api/v1/admin/orgs/{org.id}/feature-flags/{_FLAG}")
    assert response.status_code == 200

    events, _ = await AuditRepository(session).list_cursor(org.id)
    matching = [e for e in events if e.action == "admin.feature_flag.set"]
    assert len(matching) == 2  # the PUT above, then this DELETE
    delete_event = matching[0]  # list_cursor orders newest-first
    assert delete_event.diff == {"flag": _FLAG, "enabled": None}


async def test_delete_override_is_idempotent_when_no_override_exists(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    org = await _org_with_plan_flag(session)
    admin_client = await _as_superadmin(client, session)

    response = await admin_client.delete(f"/api/v1/admin/orgs/{org.id}/feature-flags/{_FLAG}")

    assert response.status_code == 200


async def test_delete_returns_404_for_nonexistent_org(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin_client = await _as_superadmin(client, session)

    response = await admin_client.delete(f"/api/v1/admin/orgs/{uuid4()}/feature-flags/{_FLAG}")

    assert response.status_code == 404
