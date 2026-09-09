"""
Verifies T-218's admin-assisted lost-2FA recovery endpoint,
POST /api/v1/admin/users/{id}/disable-totp: superadmin gating (inherited from
require_superadmin, T-140), the "nothing to disable" conflict, the actual
TOTP-clearing effect (a user can then log in without a 2FA challenge), and
that the action is fully audited at elevated severity -- same posture as
T-142's impersonation, verified the same way in test_admin_impersonation_router.py.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
import pyotp
import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.admin.service import AdminAccountRecoveryService
from app.modules.audit.models import AuditEvent
from app.modules.auth.models import User
from app.modules.auth.repository import TOTPSecretRepository, UserRepository
from app.modules.auth.schemas import LoginRequest
from app.modules.auth.service import AuthService
from app.modules.auth.utils import create_access_token, hash_password

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


async def _make_user_with_totp(session: AsyncSession) -> User:
    """A user with a fully-enabled TOTP secret -- i.e. the actual "lost my
    2FA device" scenario this endpoint exists for, not just the
    User.totp_enabled flag on its own."""
    user = await _make_user(session, totp_enabled=True)
    secret = pyotp.random_base32()
    await TOTPSecretRepository(session).upsert_pending(user.id, secret)
    await TOTPSecretRepository(session).mark_enabled(user.id, "[]")
    await session.commit()
    return user


async def _as_superadmin(
    client: httpx.AsyncClient, session: AsyncSession
) -> tuple[httpx.AsyncClient, User]:
    admin = await _make_user(session, is_superadmin=True, totp_enabled=True)
    token = create_access_token(admin.id, None, ())
    client.headers["Authorization"] = f"Bearer {token}"
    return client, admin


async def _as_non_superadmin(client: httpx.AsyncClient, session: AsyncSession) -> httpx.AsyncClient:
    user = await _make_user(session)
    token = create_access_token(user.id, None, ())
    client.headers["Authorization"] = f"Bearer {token}"
    return client


_VALID_REASON = "User lost their phone and all backup codes (ticket #4821)"


async def test_disable_totp_denies_non_superadmin_caller(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    target = await _make_user_with_totp(session)
    caller = await _as_non_superadmin(client, session)

    response = await caller.post(
        f"/api/v1/admin/users/{target.id}/disable-totp", json={"reason": _VALID_REASON}
    )

    assert response.status_code == 403


async def test_disable_totp_returns_404_for_unknown_user(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin_client, _ = await _as_superadmin(client, session)

    response = await admin_client.post(
        f"/api/v1/admin/users/{uuid4()}/disable-totp", json={"reason": _VALID_REASON}
    )

    assert response.status_code == 404


async def test_disable_totp_missing_reason_returns_422(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    target = await _make_user_with_totp(session)
    admin_client, _ = await _as_superadmin(client, session)

    response = await admin_client.post(f"/api/v1/admin/users/{target.id}/disable-totp", json={})

    assert response.status_code == 422


async def test_disable_totp_short_reason_returns_422(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    target = await _make_user_with_totp(session)
    admin_client, _ = await _as_superadmin(client, session)

    response = await admin_client.post(
        f"/api/v1/admin/users/{target.id}/disable-totp", json={"reason": "short"}
    )

    assert response.status_code == 422


async def test_disable_totp_when_not_enabled_returns_409(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    target = await _make_user(session)  # totp_enabled=False
    admin_client, _ = await _as_superadmin(client, session)

    response = await admin_client.post(
        f"/api/v1/admin/users/{target.id}/disable-totp", json={"reason": _VALID_REASON}
    )

    assert response.status_code == 409


async def test_disable_totp_happy_path_clears_totp_and_allows_login_without_2fa(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    target = await _make_user_with_totp(session)
    admin_client, _ = await _as_superadmin(client, session)

    response = await admin_client.post(
        f"/api/v1/admin/users/{target.id}/disable-totp", json={"reason": _VALID_REASON}
    )

    assert response.status_code == 200
    refetched = await UserRepository(session).get_by_id(target.id)
    assert refetched is not None
    assert refetched.totp_enabled is False

    # The service-level login gate no longer challenges for 2FA -- proof the
    # recovery actually restores normal password-only login, not just the
    # flag on the User row.
    result = await AuthService(session).login(
        LoginRequest(email=target.email, password=_PASSWORD), "127.0.0.1", "pytest"
    )
    assert isinstance(result, tuple)


async def test_disable_totp_deletes_the_totp_secret_row(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    target = await _make_user_with_totp(session)
    admin_client, _ = await _as_superadmin(client, session)

    await admin_client.post(
        f"/api/v1/admin/users/{target.id}/disable-totp", json={"reason": _VALID_REASON}
    )

    record = await TOTPSecretRepository(session).get_by_user_id(target.id)
    assert record is None


async def test_disable_totp_writes_elevated_audit_event(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    target = await _make_user_with_totp(session)
    admin_client, admin = await _as_superadmin(client, session)

    await admin_client.post(
        f"/api/v1/admin/users/{target.id}/disable-totp", json={"reason": _VALID_REASON}
    )

    result = await session.execute(
        select(AuditEvent).where(AuditEvent.action == "admin.user.disable_totp")
    )
    event = result.scalar_one()
    assert event.actor_id == admin.id
    assert event.resource_id == str(target.id)
    assert event.severity == "elevated"
    assert event.diff is not None
    assert event.diff["reason"] == _VALID_REASON


async def test_disable_totp_enqueues_security_notification_to_target(
    client: httpx.AsyncClient,
    session: AsyncSession,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    target = await _make_user_with_totp(session)
    admin_client, _ = await _as_superadmin(client, session)

    await admin_client.post(
        f"/api/v1/admin/users/{target.id}/disable-totp", json={"reason": _VALID_REASON}
    )

    email_jobs = [kwargs for name, kwargs in enqueued_jobs if name == "send_email"]
    assert len(email_jobs) == 1
    assert email_jobs[0]["to"] == target.email
    assert email_jobs[0]["context"]["event"] == "totp_disabled_by_admin"


async def test_disable_totp_not_reachable_via_totp_secret_table_race(
    session: AsyncSession,
    redis: Redis,
) -> None:
    """Direct-repository sanity check, independent of the router: a user
    with totp_enabled=True but no TOTPSecret row (shouldn't normally happen,
    but the service must not crash on it -- disable() is skipped, only the
    flag flip happens)."""
    user = await _make_user(session, totp_enabled=True)

    await AdminAccountRecoveryService(session, redis).disable_totp_for_user(
        target=user, reason=_VALID_REASON, actor_id=uuid4(), ip=None, ua=None
    )

    refetched = await UserRepository(session).get_by_id(user.id)
    assert refetched is not None
    assert refetched.totp_enabled is False
