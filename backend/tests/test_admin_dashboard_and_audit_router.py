"""
Verifies T-143's admin dashboard + global audit feed endpoints under
/api/v1/admin/: GET /dashboard (org/user counts, MRR estimate, recent
signups, recent payment failures) and GET /audit (cursor-paginated,
cross-org feed filterable by actor/action/date).

Same conftest client/session fixtures and _as_superadmin/_as_non_superadmin
convention as test_admin_directory_router.py (T-141).
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.service import AuditService
from app.modules.auth.repository import UserRepository
from app.modules.auth.utils import create_access_token, hash_password
from app.modules.billing.models import Plan, Subscription
from app.modules.tenancy.repository import OrganizationRepository
from tests._auth import session_token

# T-291: this file needs admin enabled -- its routes, its tables, or
# both. CI's module-matrix job deselects it in the combination that turns
# every optional module off.
pytestmark = pytest.mark.module_dependent

_PASSWORD = "Correct-Horse9"


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


async def _org_with_active_subscription(
    session: AsyncSession, *, billing_interval: str, free_override: bool = False
) -> None:
    org = await OrganizationRepository(session).create(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    plan = Plan(
        name="Pro",
        slug=f"pro-{uuid4().hex[:8]}",
        stripe_price_id_monthly="price_m",
        stripe_price_id_yearly="price_y",
        price_monthly=2900,
        price_yearly=29000,
        features={},
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
            billing_interval=billing_interval,
            free_override=free_override,
        )
    )
    await session.commit()


# ---- GET /dashboard -----------------------------------------------------------


async def test_get_dashboard_denies_non_superadmin(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin_client = await _as_non_superadmin(client, session)

    response = await admin_client.get("/api/v1/admin/dashboard")

    assert response.status_code == 403


async def test_get_dashboard_requires_authentication(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/admin/dashboard")

    assert response.status_code == 401


async def test_get_dashboard_counts_orgs_and_users(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await OrganizationRepository(session).create(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    await session.commit()
    admin_client = await _as_superadmin(client, session)

    response = await admin_client.get("/api/v1/admin/dashboard")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["org_count"] >= 1
    assert data["user_count"] >= 1


async def test_get_dashboard_mrr_normalizes_yearly_to_monthly(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin_client = await _as_superadmin(client, session)
    baseline = (await admin_client.get("/api/v1/admin/dashboard")).json()["data"]["mrr_cents"]

    await _org_with_active_subscription(session, billing_interval="monthly")
    await _org_with_active_subscription(session, billing_interval="yearly")
    # A comp'd (free_override) subscription must not count as revenue.
    await _org_with_active_subscription(session, billing_interval="monthly", free_override=True)

    response = await admin_client.get("/api/v1/admin/dashboard")

    assert response.status_code == 200
    mrr_cents = response.json()["data"]["mrr_cents"]
    # monthly plan contributes 2900, yearly plan contributes 29000 // 12 = 2416
    assert mrr_cents == baseline + 2900 + (29000 // 12)


async def test_get_dashboard_recent_signups_ordered_newest_first(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin_client = await _as_superadmin(client, session)
    newest = await UserRepository(session).create(
        email=f"newest-{uuid4()}@example.com",
        hashed_password=hash_password(_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    await session.commit()

    response = await admin_client.get("/api/v1/admin/dashboard")

    assert response.status_code == 200
    signups = response.json()["data"]["recent_signups"]
    assert signups[0]["id"] == str(newest.id)


async def test_get_dashboard_recent_payment_failures(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    org = await OrganizationRepository(session).create(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    await session.commit()
    await AuditService(session).log(
        action="billing.payment_failed",
        resource_type="subscription",
        resource_id=str(uuid4()),
        org_id=org.id,
    )
    await session.commit()
    admin_client = await _as_superadmin(client, session)

    response = await admin_client.get("/api/v1/admin/dashboard")

    assert response.status_code == 200
    failures = response.json()["data"]["recent_payment_failures"]
    assert any(f["org_id"] == str(org.id) for f in failures)


# ---- GET /audit -----------------------------------------------------------------


async def test_list_audit_feed_denies_non_superadmin(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin_client = await _as_non_superadmin(client, session)

    response = await admin_client.get("/api/v1/admin/audit")

    assert response.status_code == 403


async def test_list_audit_feed_filters_by_action(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    org = await OrganizationRepository(session).create(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    await session.commit()
    marker_action = f"test.marker.{uuid4().hex[:8]}"
    await AuditService(session).log(
        action=marker_action, resource_type="organization", org_id=org.id
    )
    await AuditService(session).log(
        action="test.other", resource_type="organization", org_id=org.id
    )
    await session.commit()
    admin_client = await _as_superadmin(client, session)

    response = await admin_client.get("/api/v1/admin/audit", params={"action": marker_action})

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["action"] == marker_action
    assert data[0]["org_name"] == "Acme"


async def test_list_audit_feed_filters_by_actor(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    actor = await UserRepository(session).create(
        email=f"actor-{uuid4()}@example.com",
        hashed_password=hash_password(_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    await session.commit()
    marker_action = f"test.marker.{uuid4().hex[:8]}"
    await AuditService(session).log(
        action=marker_action, resource_type="user", org_id=None, actor_id=actor.id
    )
    await AuditService(session).log(action="test.other", resource_type="user", org_id=None)
    await session.commit()
    admin_client = await _as_superadmin(client, session)

    response = await admin_client.get("/api/v1/admin/audit", params={"actor": str(actor.id)})

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["actor_id"] == str(actor.id)
    assert data[0]["actor_email"] == actor.email


async def test_list_audit_feed_filters_by_org(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    matching_org = await OrganizationRepository(session).create(
        name="Matching", slug=f"matching-{uuid4().hex[:8]}"
    )
    other_org = await OrganizationRepository(session).create(
        name="Other", slug=f"other-{uuid4().hex[:8]}"
    )
    await session.commit()
    marker_action = f"test.marker.{uuid4().hex[:8]}"
    await AuditService(session).log(
        action=marker_action, resource_type="organization", org_id=matching_org.id
    )
    await AuditService(session).log(
        action=marker_action, resource_type="organization", org_id=other_org.id
    )
    await session.commit()
    admin_client = await _as_superadmin(client, session)

    response = await admin_client.get(
        "/api/v1/admin/audit", params={"action": marker_action, "org": str(matching_org.id)}
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["org_id"] == str(matching_org.id)
    assert data[0]["org_name"] == "Matching"


async def test_list_audit_feed_paginates_without_duplicates(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    marker_action = f"test.marker.{uuid4().hex[:8]}"
    for _ in range(3):
        await AuditService(session).log(
            action=marker_action, resource_type="organization", org_id=None
        )
    await session.commit()
    admin_client = await _as_superadmin(client, session)

    page1 = await admin_client.get(
        "/api/v1/admin/audit", params={"action": marker_action, "limit": 2}
    )
    assert page1.status_code == 200
    next_cursor = page1.json()["meta"]["next_cursor"]
    assert next_cursor is not None

    page2 = await admin_client.get(
        "/api/v1/admin/audit",
        params={"action": marker_action, "limit": 2, "after": next_cursor},
    )
    assert page2.status_code == 200

    ids_page1 = {row["id"] for row in page1.json()["data"]}
    ids_page2 = {row["id"] for row in page2.json()["data"]}
    assert ids_page1.isdisjoint(ids_page2)
    assert len(ids_page1) + len(ids_page2) == 3
