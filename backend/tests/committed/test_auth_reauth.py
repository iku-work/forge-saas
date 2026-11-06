"""
Re-authentication in place: answering require_recent_auth's 403 without a
new sign-in. POST /auth/reauth takes the password or a 2FA code; accounts
with neither go through their OAuth provider with ?reauth=true, and the
callback renews the session the refresh cookie named instead of creating one.
"""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import httpx
import pyotp
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AuthenticationError, PermissionDeniedError
from app.main import app
from app.modules.auth.models import User
from app.modules.auth.oauth import OAuthClient, get_oauth_clients
from app.modules.auth.repository import (
    OAuthAccountRepository,
    SessionRepository,
    UserRepository,
)
from app.modules.auth.schemas import OAuthProfile
from app.modules.auth.service import AuthService
from app.modules.auth.utils import (
    create_access_token,
    hash_password,
    hash_token,
    verify_access_token,
)
from app.modules.sso.enforcement import SsoRequired

_PASSWORD = "Correct-Horse9"
_IP = "203.0.113.7"
_UA = "pytest"
_LONG_AGO = datetime.now(UTC) - timedelta(hours=3)


class _FakeOAuthClient:
    def __init__(self, provider_user_id: str) -> None:
        self.profile = OAuthProfile(
            provider_user_id=provider_user_id, email="x@example.com", email_verified=True
        )

    def authorization_url(self, *, redirect_uri: str, state: str) -> str:  # noqa: ARG002
        return f"https://fake-provider.example/authorize?state={state}"

    async def fetch_profile(self, *, code: str, redirect_uri: str) -> OAuthProfile:  # noqa: ARG002
        return self.profile


@pytest.fixture
async def user(session: AsyncSession) -> User:
    created = await UserRepository(session).create(
        email="reauth@example.com",
        hashed_password=hash_password(_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    await session.commit()
    return created


async def _old_session(session: AsyncSession, user: User, token_hash: str | None = None) -> UUID:
    row = await SessionRepository(session).create(
        user_id=user.id,
        token_hash=token_hash or hash_token(str(uuid4())),
        device_label=None,
        ip_address=_IP,
        user_agent=_UA,
        expires_at=datetime.now(UTC) + timedelta(days=1),
        auth_time=_LONG_AGO,
    )
    await session.commit()
    return row.id


async def _auth_time(session: AsyncSession, session_id: UUID) -> datetime:
    row = await SessionRepository(session).get_by_id(session_id)
    assert row is not None
    await session.refresh(row)
    return row.auth_time


def _is_recent(moment: datetime) -> bool:
    return datetime.now(UTC) - moment < timedelta(minutes=1)


# --- password and codes -------------------------------------------------------


async def test_the_right_password_renews_the_session(session: AsyncSession, user: User) -> None:
    sid = await _old_session(session, user)

    token = await AuthService(session).reauthenticate(
        user, sid, password=_PASSWORD, code=None, ip=_IP, user_agent=_UA
    )

    assert _is_recent(verify_access_token(token).auth_time)
    assert _is_recent(await _auth_time(session, sid))


async def test_a_wrong_password_is_refused_and_counts_toward_lockout(
    session: AsyncSession, user: User
) -> None:
    sid = await _old_session(session, user)

    with pytest.raises(AuthenticationError) as exc_info:
        await AuthService(session).reauthenticate(
            user, sid, password="wrong", code=None, ip=_IP, user_agent=_UA
        )
    await session.rollback()  # what get_session does; the count must survive it

    assert exc_info.value.message_key == "errors.reauth_failed"
    refreshed = await UserRepository(session).get_by_id(user.id)
    assert refreshed is not None
    await session.refresh(refreshed)
    assert refreshed.failed_login_count == 1
    assert await _auth_time(session, sid) == _LONG_AGO


async def test_an_account_without_a_password_cannot_use_one(session: AsyncSession) -> None:
    oauth_only = await UserRepository(session).create(
        email="google-only@example.com", hashed_password=None, email_verified_at=datetime.now(UTC)
    )
    await session.commit()
    sid = await _old_session(session, oauth_only)

    with pytest.raises(AuthenticationError):
        await AuthService(session).reauthenticate(
            oauth_only, sid, password=_PASSWORD, code=None, ip=_IP, user_agent=_UA
        )


async def test_a_totp_code_or_a_backup_code_renews_the_session(
    session: AsyncSession, user: User
) -> None:
    service = AuthService(session)
    _, secret = await service.enable_2fa(user)
    backup_codes = await service.verify_2fa(user, pyotp.TOTP(secret).now())
    await session.commit()
    sid = await _old_session(session, user)

    with pytest.raises(AuthenticationError):
        # The step verify_2fa just consumed: a replay, refused.
        await service.reauthenticate(
            user, sid, password=None, code=pyotp.TOTP(secret).now(), ip=_IP, user_agent=_UA
        )
    await session.refresh(user)  # the refusal committed, which expired it
    next_code = pyotp.TOTP(secret).at(datetime.now(UTC), 1)
    assert await service.reauthenticate(
        user, sid, password=None, code=next_code, ip=_IP, user_agent=_UA
    )
    assert await service.reauthenticate(
        user, sid, password=None, code=backup_codes[0], ip=_IP, user_agent=_UA
    )
    row = await SessionRepository(session).get_by_id(sid)
    assert row is not None
    await session.refresh(row)
    assert {"otp", "backup"} <= set(row.amr)


async def test_a_code_without_2fa_enabled_is_refused(session: AsyncSession, user: User) -> None:
    sid = await _old_session(session, user)

    with pytest.raises(AuthenticationError):
        await AuthService(session).reauthenticate(
            user, sid, password=None, code="123456", ip=_IP, user_agent=_UA
        )


async def test_an_impersonation_token_cannot_reauthenticate(
    session: AsyncSession, user: User
) -> None:
    with pytest.raises(AuthenticationError) as exc_info:
        await AuthService(session).reauthenticate(
            user, None, password=_PASSWORD, code=None, ip=_IP, user_agent=_UA
        )

    assert exc_info.value.message_key == "errors.session_invalid"


async def test_a_locked_account_is_refused_before_the_password_is_checked(
    session: AsyncSession, user: User
) -> None:
    sid = await _old_session(session, user)
    await UserRepository(session).set_lockout(user.id, datetime.now(UTC) + timedelta(minutes=5))
    await session.commit()
    await session.refresh(user)

    with pytest.raises(AuthenticationError) as exc_info:
        await AuthService(session).reauthenticate(
            user, sid, password=_PASSWORD, code=None, ip=_IP, user_agent=_UA
        )

    assert exc_info.value.message_key == "errors.account_locked"


async def test_an_sso_organization_refuses_a_password(
    session: AsyncSession, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = await _old_session(session, user)

    async def _blocked(_session: AsyncSession, _user_id: UUID) -> SsoRequired:
        return SsoRequired(provider_label="Okta", org_slug="acme")

    monkeypatch.setattr("app.modules.sso.enforcement.password_login_blocked", _blocked)

    with pytest.raises(PermissionDeniedError):
        await AuthService(session).reauthenticate(
            user, sid, password=_PASSWORD, code=None, ip=_IP, user_agent=_UA
        )


async def test_the_route_returns_a_token_that_passes_the_step_up_gate(
    client: httpx.AsyncClient, session: AsyncSession, user: User
) -> None:
    sid = await _old_session(session, user)
    stale = create_access_token(user.id, None, (), auth_time=_LONG_AGO, sid=sid)

    response = await client.post(
        "/api/v1/auth/reauth",
        json={"password": _PASSWORD},
        headers={"Authorization": f"Bearer {stale}"},
    )

    assert response.status_code == 200, response.text
    fresh = response.json()["data"]["access_token"]
    assert _is_recent(verify_access_token(fresh).auth_time)


async def test_the_route_wants_exactly_one_proof(client: httpx.AsyncClient, user: User) -> None:
    token = create_access_token(user.id, None, ())
    for body in ({}, {"password": _PASSWORD, "code": "123456"}):
        response = await client.post(
            "/api/v1/auth/reauth", json=body, headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 422


# --- OAuth ------------------------------------------------------------------------


@pytest.fixture
def oauth_clients() -> dict[str, OAuthClient]:
    return {"google": _FakeOAuthClient("g-owner")}


@pytest.fixture
def client(client: httpx.AsyncClient, oauth_clients: dict[str, OAuthClient]) -> httpx.AsyncClient:
    app.dependency_overrides[get_oauth_clients] = lambda: oauth_clients
    return client


async def _oauth_reauth(
    client: httpx.AsyncClient, refresh_token: str | None, next_path: str = "/en/settings/billing"
) -> httpx.Response:
    start = await client.get(
        "/api/v1/auth/oauth/google",
        params={"reauth": "true", "next": next_path},
        cookies={"__Host-refresh_token": refresh_token} if refresh_token else {},
    )
    state = start.cookies.get("oauth_state")
    if state is None:
        return start
    return await client.get(
        "/api/v1/auth/oauth/google/callback",
        params={"code": "x", "state": state},
        cookies={"oauth_state": state},
    )


async def _linked_session(session: AsyncSession, user: User, refresh_token: str) -> UUID:
    await OAuthAccountRepository(session).create(
        user_id=user.id, provider="google", provider_user_id="g-owner"
    )
    return await _old_session(session, user, token_hash=hash_token(refresh_token))


async def test_google_renews_the_session_the_cookie_names(
    client: httpx.AsyncClient, session: AsyncSession, user: User
) -> None:
    sid = await _linked_session(session, user, "refresh-1")

    response = await _oauth_reauth(client, "refresh-1")

    assert response.status_code == 302
    assert response.headers["location"] == f"{get_settings().FRONTEND_URL}/en/settings/billing"
    assert "__Host-refresh_token=" not in str(response.headers.get_list("set-cookie"))
    assert _is_recent(await _auth_time(session, sid))


async def test_a_different_google_account_does_not_count(
    client: httpx.AsyncClient,
    session: AsyncSession,
    user: User,
    oauth_clients: dict[str, OAuthClient],
) -> None:
    sid = await _linked_session(session, user, "refresh-2")
    oauth_clients["google"] = _FakeOAuthClient("g-someone-else")

    response = await _oauth_reauth(client, "refresh-2")

    location = urlsplit(response.headers["location"])
    assert location.path == "/en/settings/billing"
    assert parse_qs(location.query) == {"reauth_error": ["errors.reauth_wrong_account"]}
    assert await _auth_time(session, sid) == _LONG_AGO


async def test_a_session_revoked_meanwhile_does_not_count(
    client: httpx.AsyncClient, session: AsyncSession, user: User
) -> None:
    sid = await _linked_session(session, user, "refresh-4")
    start = await client.get(
        "/api/v1/auth/oauth/google",
        params={"reauth": "true"},
        cookies={"__Host-refresh_token": "refresh-4"},
    )
    await SessionRepository(session).revoke(sid)
    await session.commit()
    state = start.cookies.get("oauth_state")

    response = await client.get(
        "/api/v1/auth/oauth/google/callback",
        params={"code": "x", "state": state},
        cookies={"oauth_state": state},
    )

    assert parse_qs(urlsplit(response.headers["location"]).query) == {
        "reauth_error": ["errors.session_invalid"]
    }


class _RefusingOAuthClient(_FakeOAuthClient):
    async def fetch_profile(self, *, code: str, redirect_uri: str) -> OAuthProfile:  # noqa: ARG002
        raise AuthenticationError("errors.oauth_exchange_failed") from ValueError("invalid_grant")


async def test_a_code_the_provider_refuses_returns_to_the_page(
    client: httpx.AsyncClient,
    session: AsyncSession,
    user: User,
    oauth_clients: dict[str, OAuthClient],
) -> None:
    sid = await _linked_session(session, user, "refresh-5")
    oauth_clients["google"] = _RefusingOAuthClient("g-owner")

    response = await _oauth_reauth(client, "refresh-5")

    assert parse_qs(urlsplit(response.headers["location"]).query) == {
        "reauth_error": ["errors.oauth_exchange_failed"]
    }
    assert await _auth_time(session, sid) == _LONG_AGO


async def test_without_a_session_cookie_there_is_nothing_to_renew(
    client: httpx.AsyncClient,
) -> None:
    response = await _oauth_reauth(client, None)

    assert response.status_code == 302
    location = urlsplit(response.headers["location"])
    assert location.path == "/en/settings/billing"
    assert parse_qs(location.query) == {"reauth_error": ["errors.session_invalid"]}


async def test_next_cannot_point_at_another_site(
    client: httpx.AsyncClient, session: AsyncSession, user: User
) -> None:
    await _linked_session(session, user, "refresh-3")

    response = await _oauth_reauth(client, "refresh-3", next_path="//evil.example/x")

    assert response.headers["location"] == f"{get_settings().FRONTEND_URL}/"


async def test_a_bad_state_still_goes_to_the_login_page(client: httpx.AsyncClient) -> None:
    response = await client.get(
        "/api/v1/auth/oauth/google/callback",
        params={"code": "x", "state": "forged"},
        cookies={"oauth_state": "forged"},
    )

    location = urlsplit(response.headers["location"])
    assert location.path == "/en/login"
    assert parse_qs(location.query) == {"sign_in_error": ["errors.invalid_token"]}
