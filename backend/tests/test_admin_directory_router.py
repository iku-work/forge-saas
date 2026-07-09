"""
Verifies T-141's read-mostly admin directory endpoints under /api/v1/admin/:
GET /orgs (cursor list + q/plan/status filters), GET /orgs/{id} (detail incl.
member count/subscription), GET /users (cursor list + q/active filters), and
GET /users/{id} (detail incl. memberships/sessions/audit summary).

Same conftest client/session fixtures and _as_superadmin/_as_non_superadmin
convention as test_admin_feature_flags_router.py (T-122).
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.permissions import MemberRole
from app.modules.auth.repository import SessionRepository, UserRepository
from app.modules.auth.utils import create_access_token, hash_password
from app.modules.billing.models import Plan, Subscription
from app.modules.tenancy.models import Organization
from app.modules.tenancy.repository import MembershipRepository, OrganizationRepository

_PASSWORD = "Correct-Horse9"


async def _org_with_subscription(
    session: AsyncSession, *, plan_slug: str, status: str
) -> Organization:
    org = await OrganizationRepository(session).create(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    plan = Plan(
        name="Pro",
        slug=plan_slug,
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
            status=status,
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
    token = create_access_token(user.id, None, ())
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


# ---- GET /orgs ---------------------------------------------------------------


async def test_list_orgs_denies_non_superadmin(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin_client = await _as_non_superadmin(client, session)

    response = await admin_client.get("/api/v1/admin/orgs")

    assert response.status_code == 403


async def test_list_orgs_requires_authentication(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/admin/orgs")

    assert response.status_code == 401


async def test_list_orgs_returns_plan_slug_and_status(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    org = await _org_with_subscription(session, plan_slug=f"pro-{uuid4().hex[:8]}", status="active")
    admin_client = await _as_superadmin(client, session)

    response = await admin_client.get("/api/v1/admin/orgs", params={"q": org.slug})

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["id"] == str(org.id)
    assert data[0]["subscription_status"] == "active"


async def test_list_orgs_filters_by_status(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    active_org = await _org_with_subscription(
        session, plan_slug=f"pro-{uuid4().hex[:8]}", status="active"
    )
    past_due_org = await _org_with_subscription(
        session, plan_slug=f"pro-{uuid4().hex[:8]}", status="past_due"
    )
    admin_client = await _as_superadmin(client, session)

    response = await admin_client.get("/api/v1/admin/orgs", params={"status": "past_due"})

    assert response.status_code == 200
    ids = {row["id"] for row in response.json()["data"]}
    assert str(past_due_org.id) in ids
    assert str(active_org.id) not in ids


async def test_list_orgs_filters_by_plan(client: httpx.AsyncClient, session: AsyncSession) -> None:
    plan_slug = f"pro-{uuid4().hex[:8]}"
    matching_org = await _org_with_subscription(session, plan_slug=plan_slug, status="active")
    other_org = await _org_with_subscription(
        session, plan_slug=f"other-{uuid4().hex[:8]}", status="active"
    )
    admin_client = await _as_superadmin(client, session)

    response = await admin_client.get("/api/v1/admin/orgs", params={"plan": plan_slug})

    assert response.status_code == 200
    ids = {row["id"] for row in response.json()["data"]}
    assert str(matching_org.id) in ids
    assert str(other_org.id) not in ids


# ---- GET /orgs/{id} ------------------------------------------------------------


async def test_get_org_detail_denies_non_superadmin(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    org = await OrganizationRepository(session).create(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    await session.commit()
    admin_client = await _as_non_superadmin(client, session)

    response = await admin_client.get(f"/api/v1/admin/orgs/{org.id}")

    assert response.status_code == 403


async def test_get_org_detail_returns_404_for_nonexistent_org(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin_client = await _as_superadmin(client, session)

    response = await admin_client.get(f"/api/v1/admin/orgs/{uuid4()}")

    assert response.status_code == 404


async def test_get_org_detail_includes_member_count_and_subscription(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    org = await _org_with_subscription(session, plan_slug=f"pro-{uuid4().hex[:8]}", status="active")
    member = await UserRepository(session).create(
        email=f"member-{uuid4()}@example.com",
        hashed_password=hash_password(_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    await MembershipRepository(session).create(
        user_id=member.id, org_id=org.id, role=MemberRole.OWNER
    )
    await session.commit()
    admin_client = await _as_superadmin(client, session)

    response = await admin_client.get(f"/api/v1/admin/orgs/{org.id}")

    assert response.status_code == 200
    detail = response.json()["data"]
    assert detail["member_count"] == 1
    assert detail["subscription"]["status"] == "active"


async def test_get_org_detail_subscription_none_when_org_has_no_subscription(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    org = await OrganizationRepository(session).create(name="Bare", slug=f"bare-{uuid4().hex[:8]}")
    await session.commit()
    admin_client = await _as_superadmin(client, session)

    response = await admin_client.get(f"/api/v1/admin/orgs/{org.id}")

    assert response.status_code == 200
    detail = response.json()["data"]
    assert detail["subscription"] is None
    assert detail["member_count"] == 0


# ---- GET /users ----------------------------------------------------------------


async def test_list_users_denies_non_superadmin(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin_client = await _as_non_superadmin(client, session)

    response = await admin_client.get("/api/v1/admin/users")

    assert response.status_code == 403


async def test_list_users_filters_by_email_and_active(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    target_email = f"findme-{uuid4().hex[:8]}@example.com"
    target = await UserRepository(session).create(
        email=target_email,
        hashed_password=hash_password(_PASSWORD),
        email_verified_at=datetime.now(UTC),
        is_active=True,
    )
    inactive = await UserRepository(session).create(
        email=f"other-{uuid4()}@example.com",
        hashed_password=hash_password(_PASSWORD),
        email_verified_at=datetime.now(UTC),
        is_active=False,
    )
    await session.commit()
    admin_client = await _as_superadmin(client, session)

    response = await admin_client.get("/api/v1/admin/users", params={"q": "findme"})

    assert response.status_code == 200
    ids = {row["id"] for row in response.json()["data"]}
    assert str(target.id) in ids
    assert str(inactive.id) not in ids

    active_response = await admin_client.get("/api/v1/admin/users", params={"active": "true"})
    active_ids = {row["id"] for row in active_response.json()["data"]}
    assert str(target.id) in active_ids
    assert str(inactive.id) not in active_ids


async def test_list_users_paginates_without_duplicates(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    users = [
        await UserRepository(session).create(
            email=f"page-{uuid4()}@example.com",
            hashed_password=hash_password(_PASSWORD),
            email_verified_at=datetime.now(UTC),
        )
        for _ in range(3)
    ]
    await session.commit()
    admin_client = await _as_superadmin(client, session)

    page1 = await admin_client.get("/api/v1/admin/users", params={"limit": 2})
    assert page1.status_code == 200
    next_cursor = page1.json()["meta"]["next_cursor"]
    assert next_cursor is not None

    page2 = await admin_client.get("/api/v1/admin/users", params={"limit": 2, "after": next_cursor})
    assert page2.status_code == 200

    ids_page1 = {row["id"] for row in page1.json()["data"]}
    ids_page2 = {row["id"] for row in page2.json()["data"]}
    assert ids_page1.isdisjoint(ids_page2)
    assert {str(u.id) for u in users} <= ids_page1 | ids_page2


# ---- GET /users/{id} -------------------------------------------------------------


async def test_get_user_detail_denies_non_superadmin(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    target = await UserRepository(session).create(
        email=f"target-{uuid4()}@example.com",
        hashed_password=hash_password(_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    await session.commit()
    admin_client = await _as_non_superadmin(client, session)

    response = await admin_client.get(f"/api/v1/admin/users/{target.id}")

    assert response.status_code == 403


async def test_get_user_detail_returns_404_for_nonexistent_user(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin_client = await _as_superadmin(client, session)

    response = await admin_client.get(f"/api/v1/admin/users/{uuid4()}")

    assert response.status_code == 404


async def test_get_user_detail_includes_memberships_sessions_and_audit_summary(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    org = await OrganizationRepository(session).create(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    user = await UserRepository(session).create(
        email=f"member-{uuid4()}@example.com",
        hashed_password=hash_password(_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    await MembershipRepository(session).create(
        user_id=user.id, org_id=org.id, role=MemberRole.OWNER
    )
    await SessionRepository(session).create(
        user_id=user.id,
        token_hash=f"hash-{uuid4().hex}",
        device_label="Test Device",
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    await session.commit()
    admin_client = await _as_superadmin(client, session)

    response = await admin_client.get(f"/api/v1/admin/users/{user.id}")

    assert response.status_code == 200
    detail = response.json()["data"]
    assert len(detail["memberships"]) == 1
    assert detail["memberships"][0]["org_id"] == str(org.id)
    assert detail["memberships"][0]["role"] == "owner"
    assert len(detail["sessions"]) == 1
    assert detail["sessions"][0]["device_label"] == "Test Device"
    assert detail["audit_summary"]["total_count"] == 0
    assert detail["audit_summary"]["recent_actions"] == []


async def test_get_user_detail_skips_membership_for_deleted_org(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    org = await OrganizationRepository(session).create(
        name="Ghost", slug=f"ghost-{uuid4().hex[:8]}"
    )
    user = await UserRepository(session).create(
        email=f"member-{uuid4()}@example.com",
        hashed_password=hash_password(_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    await MembershipRepository(session).create(
        user_id=user.id, org_id=org.id, role=MemberRole.OWNER
    )
    await OrganizationRepository(session).update(org.id, deleted_at=datetime.now(UTC))
    await session.commit()
    admin_client = await _as_superadmin(client, session)

    response = await admin_client.get(f"/api/v1/admin/users/{user.id}")

    assert response.status_code == 200
    assert response.json()["data"]["memberships"] == []
