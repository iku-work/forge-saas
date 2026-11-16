"""Superadmin moderation of one account: suspend, ban, sign out everywhere,
verify the email, delete.

Suspension stops every way in (a sign-in creates no session, an access token
already issued is refused) and ends every session. A ban suspends and keeps
the address from signing up again, by hash, so it outlasts the account.
None of the destructive ones can be aimed at yourself or another superadmin.
"""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthenticationError, PermissionDeniedError
from app.modules.audit.models import AuditEvent
from app.modules.auth.models import BannedEmail, Session, User
from app.modules.auth.repository import UserRepository
from app.modules.auth.schemas import LoginRequest, OAuthProfile, RegisterRequest
from app.modules.auth.service import AuthService
from app.modules.auth.utils import banned_email_hash, hash_password
from tests._auth import session_token

# The delete path is the GDPR module's purge.
pytestmark = pytest.mark.module_dependent

_PASSWORD = "Correct-Horse9"
_REASON = "Spam uploads reported by three orgs (ticket #5120)"


async def _user(session: AsyncSession, **overrides: Any) -> User:  # noqa: ANN401 -- forwarded
    fields: dict[str, Any] = {
        "email": f"user-{uuid4()}@example.com",
        "hashed_password": hash_password(_PASSWORD),
        "email_verified_at": datetime.now(UTC),
        **overrides,
    }
    user = await UserRepository(session).create(**fields)
    await session.commit()
    return user


async def _admin_client(
    client: httpx.AsyncClient, session: AsyncSession
) -> tuple[httpx.AsyncClient, User]:
    admin = await _user(session, is_superadmin=True, totp_enabled=True)
    client.headers["Authorization"] = f"Bearer {await session_token(session, admin)}"
    return client, admin


async def _live_sessions(session: AsyncSession, user: User) -> int:
    m = cast("Any", Session)
    stmt = select(m.id).where(m.user_id == user.id, m.revoked_at.is_(None))
    return len((await session.execute(stmt)).all())


async def _actions(session: AsyncSession, user: User) -> list[str]:
    m = cast("Any", AuditEvent)
    stmt = select(m.action).where(m.resource_id == str(user.id)).order_by(m.ts)
    return [row[0] for row in (await session.execute(stmt)).all()]


async def _login(session: AsyncSession, user: User) -> None:
    email = user.email
    # The routes ran in their own session: read the user row afresh.
    session.expire_all()
    await AuthService(session).login(
        LoginRequest(email=email, password=_PASSWORD), "127.0.0.1", "test"
    )


# --- suspend ------------------------------------------------------------------


async def test_suspending_ends_sessions_and_blocks_every_way_in(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin_client, _ = await _admin_client(client, session)
    target = await _user(session)
    target_token = await session_token(session, target)

    response = await admin_client.post(
        f"/api/v1/admin/users/{target.id}/suspend", json={"reason": _REASON}
    )

    assert response.status_code == 200
    await session.refresh(target)
    assert target.suspended_at is not None
    assert await _live_sessions(session, target) == 0
    with pytest.raises(AuthenticationError) as raised:
        await _login(session, target)
    assert raised.value.message_key == "errors.account_suspended"
    # A token issued before the suspension stops working at once.
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {target_token}"})
    assert me.status_code == 401
    assert "admin.user.suspend" in await _actions(session, target)


async def test_unsuspending_lets_them_sign_in_again(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin_client, _ = await _admin_client(client, session)
    target = await _user(session, suspended_at=datetime.now(UTC))

    response = await admin_client.post(f"/api/v1/admin/users/{target.id}/unsuspend")

    assert response.status_code == 200
    await _login(session, target)


async def test_a_reason_is_required(client: httpx.AsyncClient, session: AsyncSession) -> None:
    admin_client, _ = await _admin_client(client, session)
    target = await _user(session)

    response = await admin_client.post(f"/api/v1/admin/users/{target.id}/suspend", json={})

    assert response.status_code == 422


async def test_not_on_yourself_or_another_superadmin(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin_client, admin = await _admin_client(client, session)
    other_admin = await _user(session, is_superadmin=True)

    for target in (admin, other_admin):
        for action in ("suspend", "ban", "delete"):
            response = await admin_client.post(
                f"/api/v1/admin/users/{target.id}/{action}", json={"reason": _REASON}
            )
            assert response.status_code == 403, (action, target.id)
        response = await admin_client.post(f"/api/v1/admin/users/{target.id}/sign-out-everywhere")
        assert response.status_code == 403


async def test_a_member_cannot_moderate(client: httpx.AsyncClient, session: AsyncSession) -> None:
    caller = await _user(session)
    target = await _user(session)
    client.headers["Authorization"] = f"Bearer {await session_token(session, caller)}"

    response = await client.post(
        f"/api/v1/admin/users/{target.id}/suspend", json={"reason": _REASON}
    )

    assert response.status_code == 403


# --- ban ----------------------------------------------------------------------


async def test_banning_suspends_and_keeps_the_address_out(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin_client, _ = await _admin_client(client, session)
    target = await _user(session)
    email = target.email

    response = await admin_client.post(
        f"/api/v1/admin/users/{target.id}/ban", json={"reason": _REASON}
    )
    detail = await admin_client.get(f"/api/v1/admin/users/{target.id}")

    assert response.status_code == 200
    assert detail.json()["data"]["banned"] is True
    assert detail.json()["data"]["suspended_at"] is not None
    # Only a hash of the address is kept.
    m = cast("Any", BannedEmail)
    stored = (await session.execute(select(m.email_hash))).scalars().all()
    assert banned_email_hash(email) in stored
    assert email not in stored
    assert "admin.user.ban" in await _actions(session, target)


async def test_a_banned_address_cannot_sign_up_again(session: AsyncSession) -> None:
    """Even after the account is gone, and in any letter case."""
    from app.modules.auth.repository import BannedEmailRepository  # noqa: PLC0415

    email = f"Gone-{uuid4()}@Example.com"
    await BannedEmailRepository(session).ban(banned_email_hash(email))
    auth = AuthService(session)

    await auth.register(RegisterRequest(email=email.lower(), password=_PASSWORD))
    assert await UserRepository(session).get_by_email(email.lower()) is None

    with pytest.raises(PermissionDeniedError) as raised:
        await auth.oauth_login(
            "google",
            OAuthProfile(
                provider_user_id="g-1",
                email=email.lower(),
                email_verified=True,
                full_name=None,
                avatar_url=None,
            ),
            "127.0.0.1",
            "test",
            signups_open=True,
        )
    assert raised.value.message_key == "errors.email_not_allowed"


async def test_unbanning_lets_them_back(client: httpx.AsyncClient, session: AsyncSession) -> None:
    admin_client, _ = await _admin_client(client, session)
    target = await _user(session)
    await admin_client.post(f"/api/v1/admin/users/{target.id}/ban", json={"reason": _REASON})

    response = await admin_client.post(f"/api/v1/admin/users/{target.id}/unban")
    detail = await admin_client.get(f"/api/v1/admin/users/{target.id}")

    assert response.status_code == 200
    assert detail.json()["data"]["banned"] is False
    assert detail.json()["data"]["suspended_at"] is None
    await _login(session, target)


async def test_a_lifted_ban_can_be_put_back(session: AsyncSession) -> None:
    from app.modules.auth.repository import BannedEmailRepository  # noqa: PLC0415

    bans = BannedEmailRepository(session)
    email_hash = banned_email_hash(f"again-{uuid4()}@example.com")

    await bans.ban(email_hash)
    await bans.unban(email_hash)
    assert await bans.is_banned(email_hash) is False
    await bans.ban(email_hash)

    assert await bans.is_banned(email_hash) is True


async def test_an_account_cannot_move_onto_a_banned_address(
    session: AsyncSession, redis_client: Redis
) -> None:
    """Silently, like moving onto an address someone already has."""
    from app.modules.auth.models import EmailChangeToken  # noqa: PLC0415
    from app.modules.auth.repository import BannedEmailRepository  # noqa: PLC0415

    user = await _user(session)
    banned = f"banned-{uuid4()}@example.com"
    await BannedEmailRepository(session).ban(banned_email_hash(banned))

    await AuthService(session).request_email_change(
        user, banned, _PASSWORD, "127.0.0.1", "test", redis_client
    )

    m = cast("Any", EmailChangeToken)
    tokens = (await session.execute(select(m.id).where(m.user_id == user.id))).all()
    assert tokens == []


# --- sign out, verify, delete -------------------------------------------------


async def test_signing_out_everywhere_ends_every_session(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin_client, _ = await _admin_client(client, session)
    target = await _user(session)
    await session_token(session, target)
    await session_token(session, target)

    response = await admin_client.post(f"/api/v1/admin/users/{target.id}/sign-out-everywhere")

    assert response.status_code == 200
    assert await _live_sessions(session, target) == 0
    await _login(session, target)  # not a suspension


async def test_verifying_an_email(client: httpx.AsyncClient, session: AsyncSession) -> None:
    admin_client, _ = await _admin_client(client, session)
    target = await _user(session, email_verified_at=None, is_active=False)

    first = await admin_client.post(f"/api/v1/admin/users/{target.id}/verify-email")
    again = await admin_client.post(f"/api/v1/admin/users/{target.id}/verify-email")

    assert first.status_code == 200
    assert again.status_code == 409
    await session.refresh(target)
    assert target.email_verified_at is not None
    assert target.is_active is True


async def test_deleting_an_account(client: httpx.AsyncClient, session: AsyncSession) -> None:
    admin_client, _ = await _admin_client(client, session)
    target = await _user(session)
    await session_token(session, target)

    response = await admin_client.post(
        f"/api/v1/admin/users/{target.id}/delete", json={"reason": _REASON}
    )

    assert response.status_code == 200
    await session.refresh(target)
    assert target.deleted_at is not None
    assert await _live_sessions(session, target) == 0
    assert "admin.user.delete" in await _actions(session, target)
