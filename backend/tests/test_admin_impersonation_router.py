"""
Verifies T-142's impersonation endpoints under /api/v1/admin/users/{id}/impersonate
and /api/v1/admin/impersonate/end: superadmin gating, the defence-in-depth
"can't impersonate another superadmin" check, the minted token's shape
(impersonator_id claim, hard-capped exp, never carries a refresh cookie),
the Redis jti-blocklist revocation on end, and that every audit event
written during an impersonated request is auto-stamped with impersonator_id
via app.core.audit_context / app.middleware.impersonation.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.admin.service import AdminImpersonationService
from app.modules.audit.models import AuditEvent
from app.modules.auth.models import User
from app.modules.auth.repository import UserRepository
from app.modules.auth.utils import create_access_token, hash_password, verify_access_token

# T-291: this file needs admin enabled -- its routes, its tables, or
# both. CI's module-matrix job deselects it in the combination that turns
# every optional module off.
pytestmark = pytest.mark.module_dependent

_PASSWORD = "Correct-Horse9"


@pytest.fixture
def redis(_redis_client: Redis) -> Redis:
    return _redis_client


async def _make_user(
    session: AsyncSession, *, is_superadmin: bool = False, totp_enabled: bool = False
) -> User:
    user = await UserRepository(session).create(
        email=f"user-{uuid4()}@example.com",
        hashed_password=hash_password(_PASSWORD),
        email_verified_at=datetime.now(UTC),
        is_superadmin=is_superadmin,
        totp_enabled=totp_enabled,
    )
    await session.commit()
    return user


async def _as_superadmin(
    client: httpx.AsyncClient, session: AsyncSession
) -> tuple[httpx.AsyncClient, User]:
    # T-218: require_superadmin now also gates on 2FA being enrolled -- see
    # test_dependencies.py's dedicated tests for that check itself.
    admin = await _make_user(session, is_superadmin=True, totp_enabled=True)
    token = create_access_token(admin.id, None, ())
    client.headers["Authorization"] = f"Bearer {token}"
    return client, admin


async def _as_non_superadmin(client: httpx.AsyncClient, session: AsyncSession) -> httpx.AsyncClient:
    user = await _make_user(session)
    token = create_access_token(user.id, None, ())
    client.headers["Authorization"] = f"Bearer {token}"
    return client


# ---- POST /users/{id}/impersonate --------------------------------------------


async def test_impersonate_denies_non_superadmin_caller(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    target = await _make_user(session)
    caller = await _as_non_superadmin(client, session)

    response = await caller.post(
        f"/api/v1/admin/users/{target.id}/impersonate",
        json={"reason": "Investigating a bug report", "duration_minutes": 15},
    )

    assert response.status_code == 403


async def test_impersonate_returns_404_for_unknown_user(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin_client, _ = await _as_superadmin(client, session)

    response = await admin_client.post(
        f"/api/v1/admin/users/{uuid4()}/impersonate",
        json={"reason": "Investigating a bug report", "duration_minutes": 15},
    )

    assert response.status_code == 404


async def test_impersonate_another_superadmin_returns_403(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    target = await _make_user(session, is_superadmin=True)
    admin_client, _ = await _as_superadmin(client, session)

    response = await admin_client.post(
        f"/api/v1/admin/users/{target.id}/impersonate",
        json={"reason": "Investigating a bug report", "duration_minutes": 15},
    )

    assert response.status_code == 403


async def test_impersonate_missing_reason_returns_422(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    target = await _make_user(session)
    admin_client, _ = await _as_superadmin(client, session)

    response = await admin_client.post(
        f"/api/v1/admin/users/{target.id}/impersonate", json={"duration_minutes": 15}
    )

    assert response.status_code == 422


async def test_impersonate_short_reason_returns_422(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    target = await _make_user(session)
    admin_client, _ = await _as_superadmin(client, session)

    response = await admin_client.post(
        f"/api/v1/admin/users/{target.id}/impersonate",
        json={"reason": "short", "duration_minutes": 15},
    )

    assert response.status_code == 422


async def test_impersonate_duration_over_60_returns_422(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    target = await _make_user(session)
    admin_client, _ = await _as_superadmin(client, session)

    response = await admin_client.post(
        f"/api/v1/admin/users/{target.id}/impersonate",
        json={"reason": "Investigating a bug report", "duration_minutes": 61},
    )

    assert response.status_code == 422


async def test_impersonate_issues_token_with_impersonator_claim_and_elevated_audit_event(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    target = await _make_user(session)
    admin_client, admin = await _as_superadmin(client, session)

    response = await admin_client.post(
        f"/api/v1/admin/users/{target.id}/impersonate",
        json={"reason": "Investigating a bug report", "duration_minutes": 15},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["user"]["id"] == str(target.id)

    payload = verify_access_token(data["access_token"])
    assert payload.user_id == target.id
    assert payload.impersonator_id == admin.id

    # Impersonation events have org_id=None, so AuditRepository's org-scoped
    # list_cursor doesn't apply here -- query the table directly instead.
    result = await session.execute(
        select(AuditEvent).where(AuditEvent.action == "admin.impersonate.start")
    )
    matching = result.scalars().all()
    assert len(matching) == 1
    assert matching[0].severity == "elevated"
    assert matching[0].actor_id == admin.id
    # The superadmin's own real token, not impersonating yet.
    assert matching[0].impersonator_id is None
    assert matching[0].diff == {"reason": "Investigating a bug report", "duration_minutes": 15}


async def test_impersonation_token_works_for_ordinary_authenticated_routes(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    target = await _make_user(session)
    admin_client, _ = await _as_superadmin(client, session)

    response = await admin_client.post(
        f"/api/v1/admin/users/{target.id}/impersonate",
        json={"reason": "Investigating a bug report", "duration_minutes": 15},
    )
    access_token = response.json()["data"]["access_token"]

    client.headers["Authorization"] = f"Bearer {access_token}"
    me_response = await client.get("/api/v1/auth/me")

    assert me_response.status_code == 200
    assert me_response.json()["data"]["id"] == str(target.id)


async def test_impersonation_token_cannot_be_refreshed(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    target = await _make_user(session)
    admin_client, _ = await _as_superadmin(client, session)

    response = await admin_client.post(
        f"/api/v1/admin/users/{target.id}/impersonate",
        json={"reason": "Investigating a bug report", "duration_minutes": 15},
    )
    access_token = response.json()["data"]["access_token"]

    # Impersonation never sets a refresh_token cookie, so /auth/refresh has
    # nothing to rotate regardless of which bearer token is attached.
    client.headers["Authorization"] = f"Bearer {access_token}"
    refresh_response = await client.post("/api/v1/auth/refresh")

    assert refresh_response.status_code == 401


# ---- POST /impersonate/end ----------------------------------------------------


async def test_ending_impersonation_revokes_token_for_reuse(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    target = await _make_user(session)
    admin_client, _ = await _as_superadmin(client, session)

    start_response = await admin_client.post(
        f"/api/v1/admin/users/{target.id}/impersonate",
        json={"reason": "Investigating a bug report", "duration_minutes": 15},
    )
    access_token = start_response.json()["data"]["access_token"]
    client.headers["Authorization"] = f"Bearer {access_token}"

    end_response = await client.post("/api/v1/admin/impersonate/end")
    assert end_response.status_code == 200

    me_response = await client.get("/api/v1/auth/me")
    assert me_response.status_code == 401


async def test_ending_impersonation_writes_elevated_audit_event_stamped_with_impersonator(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    target = await _make_user(session)
    admin_client, admin = await _as_superadmin(client, session)

    start_response = await admin_client.post(
        f"/api/v1/admin/users/{target.id}/impersonate",
        json={"reason": "Investigating a bug report", "duration_minutes": 15},
    )
    access_token = start_response.json()["data"]["access_token"]
    client.headers["Authorization"] = f"Bearer {access_token}"

    end_response = await client.post("/api/v1/admin/impersonate/end")
    assert end_response.status_code == 200

    result = await session.execute(
        select(AuditEvent).where(AuditEvent.action == "admin.impersonate.end")
    )
    matching = result.scalars().all()
    assert len(matching) == 1
    assert matching[0].severity == "elevated"
    assert matching[0].actor_id == target.id
    assert matching[0].impersonator_id == admin.id


async def test_ending_impersonation_missing_bearer_token_returns_401(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/api/v1/admin/impersonate/end")

    assert response.status_code == 401


async def test_ending_impersonation_with_a_non_impersonation_token_is_rejected(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    # A superadmin's own ordinary login token has no impersonator_id claim --
    # calling /impersonate/end with it is a caller error, not a valid "end my
    # own impersonation" request.
    admin_client, _ = await _as_superadmin(client, session)

    response = await admin_client.post("/api/v1/admin/impersonate/end")

    assert response.status_code == 400


# ---- Service-level hard cap (defence in depth beyond schema's le=60) ---------


async def test_service_start_caps_duration_even_if_schema_bypassed(
    session: AsyncSession, redis: Redis
) -> None:
    admin = await _make_user(session, is_superadmin=True)
    target = await _make_user(session)

    access_token, expires_at = await AdminImpersonationService(session, redis).start(
        superadmin=admin,
        target=target,
        reason="Direct service call bypassing the request schema",
        duration_minutes=999,
        ip="127.0.0.1",
        ua="pytest",
    )

    assert expires_at - datetime.now(UTC) <= timedelta(minutes=60, seconds=5)
    payload = verify_access_token(access_token)
    assert payload.impersonator_id == admin.id
