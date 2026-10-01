"""
Verifies T-022 (auth router): the full HTTP surface under /api/v1/auth --
cookie attributes on /login, rate limiting on the 6th call, 401 on a missing
refresh cookie, and cookie clearing on /logout -- plus a happy-path and a
key error-path test for every other endpoint in the table. Uses the same
real-Postgres + real-Redis testcontainers pattern as test_dependencies.py
and test_rate_limit.py: a fresh AsyncSession per request (mirroring
app.core.db.get_session in production) so each router commit is actually
visible to the next request, and the slowapi limiter's storage swapped to a
disposable Redis so /login's 5-per-minute limit can be exercised directly.

Uses httpx.AsyncClient(transport=ASGITransport(...)) rather than
fastapi.testclient.TestClient -- TestClient drives the ASGI app from a
*second* event loop (an anyio portal in a background thread), which both
breaks asyncpg's loop-bound connections and is invisible to coverage.py's
default tracer past the first `await`. AsyncClient runs the app on this
same test's event loop, so it needs none of those workarounds.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)
from sqlmodel import col, select

from app.core.config import get_settings
from app.modules.audit.models import AuditEvent
from app.modules.auth.models import (
    User,
)
from app.modules.auth.repository import (
    EmailVerificationTokenRepository,
    MagicLinkTokenRepository,
    PasswordResetTokenRepository,
    SessionRepository,
    UserRepository,
)
from app.modules.auth.utils import create_access_token, hash_password, hash_token

_VALID_PASSWORD = "Correct-Horse9"
_REFRESH_COOKIE_NAME = "__Host-refresh_token"
_PLATFORM_HEADER = "X-Client-Platform"


@pytest.fixture
def user_repo(session: AsyncSession) -> UserRepository:
    return UserRepository(session)


@pytest.fixture
def session_repo(session: AsyncSession) -> SessionRepository:
    return SessionRepository(session)


@pytest.fixture
def email_token_repo(session: AsyncSession) -> EmailVerificationTokenRepository:
    return EmailVerificationTokenRepository(session)


@pytest.fixture
def password_reset_repo(session: AsyncSession) -> PasswordResetTokenRepository:
    return PasswordResetTokenRepository(session)


@pytest.fixture
def magic_link_repo(session: AsyncSession) -> MagicLinkTokenRepository:
    return MagicLinkTokenRepository(session)


@pytest.fixture
async def verified_user(user_repo: UserRepository, session: AsyncSession) -> User:
    user = await user_repo.create(
        email="alice@example.com",
        hashed_password=hash_password(_VALID_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    await session.commit()
    return user


def _auth_header(user: User) -> dict[str, str]:
    token = create_access_token(user.id, None, ())
    return {"Authorization": f"Bearer {token}"}


def _stale_auth_header(user: User) -> dict[str, str]:
    # T-218: auth_time well outside require_recent_auth's step-up window.
    stale_auth_time = datetime.now(UTC) - timedelta(minutes=30)
    token = create_access_token(user.id, None, (), auth_time=stale_auth_time)
    return {"Authorization": f"Bearer {token}"}


# --- /register ---------------------------------------------------------------


async def test_register_returns_200_with_no_cookie(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register", json={"email": "new@example.com", "password": _VALID_PASSWORD}
    )

    assert response.status_code == 200
    assert response.json()["data"] is None
    assert "set-cookie" not in response.headers


async def test_register_duplicate_email_same_response_shape(client: httpx.AsyncClient) -> None:
    body = {"email": "dupe@example.com", "password": _VALID_PASSWORD}
    first = await client.post("/api/v1/auth/register", json=body)

    second = await client.post("/api/v1/auth/register", json=body)

    assert first.status_code == second.status_code == 200
    assert first.json()["data"] == second.json()["data"] is None
    assert first.json()["error"] == second.json()["error"] is None


# --- /login --------------------------------------------------------------------


async def test_login_sets_cookie_with_correct_attributes(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    response = await client.post(
        "/api/v1/auth/login", json={"email": verified_user.email, "password": _VALID_PASSWORD}
    )

    assert response.status_code == 200
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 1
    cookie = cookies[0]
    assert cookie.startswith(f"{_REFRESH_COOKIE_NAME}=")
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=strict" in cookie
    # T-216: Path=/ is required by the __Host- cookie-name prefix (a browser
    # refuses to set the cookie otherwise) -- see router.py's _REFRESH_COOKIE_PATH.
    assert "Path=/" in cookie


async def test_login_response_contains_access_token_and_user(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    response = await client.post(
        "/api/v1/auth/login", json={"email": verified_user.email, "password": _VALID_PASSWORD}
    )

    data = response.json()["data"]
    assert data["access_token"]
    assert data["user"]["email"] == verified_user.email


async def test_login_without_mobile_header_omits_refresh_token_from_body(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    response = await client.post(
        "/api/v1/auth/login", json={"email": verified_user.email, "password": _VALID_PASSWORD}
    )

    assert response.json()["data"]["refresh_token"] is None


async def test_login_with_mobile_header_returns_refresh_token_in_body(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": verified_user.email, "password": _VALID_PASSWORD},
        headers={_PLATFORM_HEADER: "mobile"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["refresh_token"]


async def test_login_wrong_password_returns_401(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    response = await client.post(
        "/api/v1/auth/login", json={"email": verified_user.email, "password": "wrong-password"}
    )

    assert response.status_code == 401
    assert response.json()["data"] is None
    assert response.json()["error"]["code"] == "authentication_failed"


async def test_sixth_login_call_within_a_minute_is_rate_limited(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    body = {"email": verified_user.email, "password": "wrong-password"}
    responses = [await client.post("/api/v1/auth/login", json=body) for _ in range(6)]

    assert [r.status_code for r in responses[:5]] == [401] * 5
    assert responses[5].status_code == 429
    assert responses[5].json()["error"]["code"] == "rate_limited"


# --- /refresh ------------------------------------------------------------------


async def test_refresh_without_cookie_returns_401(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/auth/refresh")

    assert response.status_code == 401


async def test_refresh_with_invalid_cookie_returns_401(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/refresh", cookies={_REFRESH_COOKIE_NAME: "not-a-real-token"}
    )

    assert response.status_code == 401


async def test_refresh_happy_path_rotates_cookie_and_returns_new_access_token(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    login_response = await client.post(
        "/api/v1/auth/login", json={"email": verified_user.email, "password": _VALID_PASSWORD}
    )
    old_access_token = login_response.json()["data"]["access_token"]

    refresh_response = await client.post("/api/v1/auth/refresh")

    assert refresh_response.status_code == 200
    new_access_token = refresh_response.json()["data"]["access_token"]
    assert new_access_token != old_access_token
    assert refresh_response.headers.get_list("set-cookie")


async def test_refresh_reused_old_cookie_after_rotation_returns_401(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    login_response = await client.post(
        "/api/v1/auth/login", json={"email": verified_user.email, "password": _VALID_PASSWORD}
    )
    old_cookie = login_response.headers.get_list("set-cookie")[0].split(";")[0].split("=", 1)[1]
    await client.post("/api/v1/auth/refresh")  # rotates -- old_cookie is now revoked
    # Drop the jar's now-rotated cookie so the explicit `cookies=` below is
    # the only refresh_token sent -- otherwise both would be on the wire.
    client.cookies.clear()

    response = await client.post("/api/v1/auth/refresh", cookies={_REFRESH_COOKIE_NAME: old_cookie})

    assert response.status_code == 401


async def test_refresh_via_body_token_without_cookie_succeeds_for_mobile(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": verified_user.email, "password": _VALID_PASSWORD},
        headers={_PLATFORM_HEADER: "mobile"},
    )
    refresh_token = login_response.json()["data"]["refresh_token"]
    client.cookies.clear()  # mobile has no cookie jar -- only the body token

    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
        headers={_PLATFORM_HEADER: "mobile"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["access_token"]
    assert data["refresh_token"]
    assert data["refresh_token"] != refresh_token


async def test_refresh_without_mobile_header_omits_refresh_token_from_body(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    await client.post(
        "/api/v1/auth/login", json={"email": verified_user.email, "password": _VALID_PASSWORD}
    )

    response = await client.post("/api/v1/auth/refresh")

    assert response.json()["data"]["refresh_token"] is None


# --- /logout / /logout-all ------------------------------------------------------


async def test_logout_clears_cookie(client: httpx.AsyncClient, verified_user: User) -> None:
    await client.post(
        "/api/v1/auth/login", json={"email": verified_user.email, "password": _VALID_PASSWORD}
    )

    response = await client.post("/api/v1/auth/logout")

    assert response.status_code == 200
    cookie = response.headers.get_list("set-cookie")[0]
    assert cookie.startswith(f"{_REFRESH_COOKIE_NAME}=")
    assert "Max-Age=0" in cookie


async def test_logout_without_cookie_is_a_no_op_200(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/auth/logout")

    assert response.status_code == 200


async def test_logout_via_body_token_without_cookie_revokes_session(
    client: httpx.AsyncClient, verified_user: User, session_repo: SessionRepository
) -> None:
    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": verified_user.email, "password": _VALID_PASSWORD},
        headers={_PLATFORM_HEADER: "mobile"},
    )
    refresh_token = login_response.json()["data"]["refresh_token"]
    client.cookies.clear()

    response = await client.post("/api/v1/auth/logout", json={"refresh_token": refresh_token})

    assert response.status_code == 200
    active = await session_repo.list_active_for_user(verified_user.id)
    assert active == []


async def test_logout_all_without_bearer_token_returns_401(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/auth/logout-all")

    assert response.status_code == 401


async def test_logout_all_revokes_every_session(
    client: httpx.AsyncClient, verified_user: User, session_repo: SessionRepository
) -> None:
    await client.post(
        "/api/v1/auth/login", json={"email": verified_user.email, "password": _VALID_PASSWORD}
    )
    client.cookies.clear()
    await client.post(
        "/api/v1/auth/login", json={"email": verified_user.email, "password": _VALID_PASSWORD}
    )

    response = await client.post("/api/v1/auth/logout-all", headers=_auth_header(verified_user))

    assert response.status_code == 200
    active = await session_repo.list_active_for_user(verified_user.id)
    assert active == []


# --- /verify-email ---------------------------------------------------------------


async def test_verify_email_happy_path(
    client: httpx.AsyncClient,
    user_repo: UserRepository,
    email_token_repo: EmailVerificationTokenRepository,
    session: AsyncSession,
) -> None:
    user = await user_repo.create(
        email="unverified@example.com", hashed_password=hash_password(_VALID_PASSWORD)
    )
    await email_token_repo.create(
        user_id=user.id,
        token_hash=hash_token("plain-verify-token"),
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    await session.commit()

    response = await client.post("/api/v1/auth/verify-email", json={"token": "plain-verify-token"})

    assert response.status_code == 200
    refetched = await user_repo.get_by_email("unverified@example.com")
    assert refetched is not None
    assert refetched.is_active is True


async def test_verify_email_invalid_token_returns_401(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/auth/verify-email", json={"token": "not-a-real-token"})

    assert response.status_code == 401


# --- /forgot-password / /reset-password -----------------------------------------


async def test_forgot_password_unknown_email_returns_200(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "ghost@example.com"}
    )

    assert response.status_code == 200
    assert response.json()["data"] is None


async def test_forgot_password_known_email_returns_200(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    response = await client.post(
        "/api/v1/auth/forgot-password", json={"email": verified_user.email}
    )

    assert response.status_code == 200
    assert response.json()["data"] is None


async def test_reset_password_happy_path_then_old_password_rejected(
    client: httpx.AsyncClient,
    verified_user: User,
    password_reset_repo: PasswordResetTokenRepository,
    session: AsyncSession,
) -> None:
    await password_reset_repo.create(
        user_id=verified_user.id,
        token_hash=hash_token("plain-reset-token"),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    await session.commit()

    response = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": "plain-reset-token", "new_password": "Even-Better-Horse8"},
    )

    assert response.status_code == 200
    login_with_old = await client.post(
        "/api/v1/auth/login", json={"email": verified_user.email, "password": _VALID_PASSWORD}
    )
    assert login_with_old.status_code == 401


async def test_reset_password_invalid_token_returns_401(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": "not-a-real-token", "new_password": "Even-Better-Horse8"},
    )

    assert response.status_code == 401


# --- /magic-link / /magic-link/consume -------------------------------------------


async def test_request_magic_link_returns_200_for_any_email(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/auth/magic-link", json={"email": "ghost@example.com"})

    assert response.status_code == 200
    assert response.json()["data"] is None


async def test_consume_magic_link_happy_path_sets_cookie(
    client: httpx.AsyncClient,
    verified_user: User,
    magic_link_repo: MagicLinkTokenRepository,
    session: AsyncSession,
) -> None:
    await magic_link_repo.create(
        user_id=verified_user.id,
        token_hash=hash_token("plain-magic-token"),
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    await session.commit()

    response = await client.post(
        "/api/v1/auth/magic-link/consume", json={"token": "plain-magic-token"}
    )

    assert response.status_code == 200
    assert response.json()["data"]["access_token"]
    assert response.headers.get_list("set-cookie")


async def test_consume_magic_link_invalid_token_returns_401(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/magic-link/consume", json={"token": "not-a-real-token"}
    )

    assert response.status_code == 401


# --- /me -----------------------------------------------------------------------


async def test_me_without_bearer_token_returns_401(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/auth/me")

    assert response.status_code == 401


async def test_me_returns_current_user(client: httpx.AsyncClient, verified_user: User) -> None:
    response = await client.get("/api/v1/auth/me", headers=_auth_header(verified_user))

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["email"] == verified_user.email
    # T-143: the web app's _admin route guard reads this off UserPublic.
    assert body["is_superadmin"] is False


# --- PATCH /me ---------------------------------------------------------------


async def test_update_me_without_bearer_token_returns_401(client: httpx.AsyncClient) -> None:
    response = await client.patch("/api/v1/auth/me", json={"locale": "fi"})

    assert response.status_code == 401


async def test_update_me_updates_locale(client: httpx.AsyncClient, verified_user: User) -> None:
    response = await client.patch(
        "/api/v1/auth/me", json={"locale": "fi"}, headers=_auth_header(verified_user)
    )

    assert response.status_code == 200
    assert response.json()["data"]["locale"] == "fi"

    me_response = await client.get("/api/v1/auth/me", headers=_auth_header(verified_user))
    assert me_response.json()["data"]["locale"] == "fi"


async def test_update_me_sets_the_full_name(client: httpx.AsyncClient, verified_user: User) -> None:
    """The profile page displayed a name with nothing able to change it: this
    endpoint took `locale` and nothing else until 2026-09-12."""
    response = await client.patch(
        "/api/v1/auth/me",
        json={"full_name": "Ada Lovelace"},
        headers=_auth_header(verified_user),
    )

    assert response.status_code == 200
    assert response.json()["data"]["full_name"] == "Ada Lovelace"

    me_response = await client.get("/api/v1/auth/me", headers=_auth_header(verified_user))
    assert me_response.json()["data"]["full_name"] == "Ada Lovelace"


async def test_update_me_leaves_out_what_was_not_sent(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    """A locale-only request must not wipe the name.

    Both fields are nullable, so if the router filtered on None instead of
    "was it sent", every locale change would clear the user's name -- and the
    locale switcher on the profile page sends exactly that request.
    """
    await client.patch(
        "/api/v1/auth/me",
        json={"full_name": "Ada Lovelace"},
        headers=_auth_header(verified_user),
    )

    response = await client.patch(
        "/api/v1/auth/me", json={"locale": "fi"}, headers=_auth_header(verified_user)
    )

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["locale"] == "fi"
    assert body["full_name"] == "Ada Lovelace"


async def test_update_me_clears_the_name_when_explicitly_null(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    """The counterpart to the test above: null is a value, not an absence."""
    await client.patch(
        "/api/v1/auth/me",
        json={"full_name": "Ada Lovelace"},
        headers=_auth_header(verified_user),
    )

    response = await client.patch(
        "/api/v1/auth/me", json={"full_name": None}, headers=_auth_header(verified_user)
    )

    assert response.status_code == 200
    assert response.json()["data"]["full_name"] is None


async def test_update_me_treats_a_whitespace_name_as_cleared(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    """A form's empty input arrives as "" or "   ", and a name made of spaces
    renders as a blank gap in the app shell rather than as no name."""
    response = await client.patch(
        "/api/v1/auth/me", json={"full_name": "   "}, headers=_auth_header(verified_user)
    )

    assert response.status_code == 200
    assert response.json()["data"]["full_name"] is None


async def test_update_me_rejects_an_overlong_name(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    response = await client.patch(
        "/api/v1/auth/me", json={"full_name": "a" * 201}, headers=_auth_header(verified_user)
    )

    assert response.status_code == 422


async def test_update_me_with_an_empty_body_is_a_no_op(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    """Not an error: the router returns the caller unchanged without touching
    the database."""
    response = await client.patch("/api/v1/auth/me", json={}, headers=_auth_header(verified_user))

    assert response.status_code == 200
    assert response.json()["data"]["email"] == verified_user.email


async def test_update_me_rejects_an_explicitly_null_locale(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    """Nullable in the schema only so the field can be omitted.

    User.locale is NOT NULL and every user has one, so unlike full_name there
    is no "clear it" to express. Letting a null through wrote NULL into the
    column and answered 500 -- found by the schemathesis contract test, which
    sends exactly this.
    """
    response = await client.patch(
        "/api/v1/auth/me", json={"locale": None}, headers=_auth_header(verified_user)
    )

    assert response.status_code == 422

    me_response = await client.get("/api/v1/auth/me", headers=_auth_header(verified_user))
    assert me_response.json()["data"]["locale"] is not None


async def test_update_me_rejects_unsupported_locale(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    response = await client.patch(
        "/api/v1/auth/me", json={"locale": "xx"}, headers=_auth_header(verified_user)
    )

    assert response.status_code == 422


async def test_update_me_defaults_theme_to_system(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    """F-004: a user who has never chosen follows prefers-color-scheme.

    Asserted on the read path rather than the model, because the web app's
    ThemeProvider reads it from exactly here -- an absent field would send it
    to its own fallback and the two could drift.
    """
    response = await client.get("/api/v1/auth/me", headers=_auth_header(verified_user))

    assert response.status_code == 200
    assert response.json()["data"]["theme"] == "system"


async def test_update_me_updates_theme(client: httpx.AsyncClient, verified_user: User) -> None:
    response = await client.patch(
        "/api/v1/auth/me", json={"theme": "dark"}, headers=_auth_header(verified_user)
    )

    assert response.status_code == 200
    assert response.json()["data"]["theme"] == "dark"

    me_response = await client.get("/api/v1/auth/me", headers=_auth_header(verified_user))
    assert me_response.json()["data"]["theme"] == "dark"


async def test_update_me_theme_does_not_disturb_the_other_fields(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    """The theme toggle sends `{"theme": ...}` alone, from a page that is not
    the profile form -- so it must not clear the name the profile form set."""
    await client.patch(
        "/api/v1/auth/me",
        json={"full_name": "Ada Lovelace", "locale": "fi"},
        headers=_auth_header(verified_user),
    )

    response = await client.patch(
        "/api/v1/auth/me", json={"theme": "light"}, headers=_auth_header(verified_user)
    )

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["theme"] == "light"
    assert body["full_name"] == "Ada Lovelace"
    assert body["locale"] == "fi"


async def test_update_me_rejects_an_explicitly_null_theme(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    """Same reasoning as the null-locale case above: User.theme is NOT NULL,
    "no preference" is spelled "system", and schemathesis sends this."""
    response = await client.patch(
        "/api/v1/auth/me", json={"theme": None}, headers=_auth_header(verified_user)
    )

    assert response.status_code == 422

    me_response = await client.get("/api/v1/auth/me", headers=_auth_header(verified_user))
    assert me_response.json()["data"]["theme"] is not None


async def test_update_me_rejects_an_unsupported_theme(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    """The column is a plain string, so this validator is the only thing
    standing between the API and a `.dark`-class value the web app cannot
    resolve."""
    response = await client.patch(
        "/api/v1/auth/me", json={"theme": "solarized"}, headers=_auth_header(verified_user)
    )

    assert response.status_code == 422


# --- /sessions -------------------------------------------------------------------


async def test_list_sessions_without_bearer_token_returns_401(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/auth/sessions")

    assert response.status_code == 401


async def test_list_sessions_returns_active_sessions(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    await client.post(
        "/api/v1/auth/login", json={"email": verified_user.email, "password": _VALID_PASSWORD}
    )

    response = await client.get("/api/v1/auth/sessions", headers=_auth_header(verified_user))

    assert response.status_code == 200
    sessions = response.json()["data"]
    assert len(sessions) == 1
    assert sessions[0]["ip_address"]


async def test_revoke_session_for_unknown_id_returns_404(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    response = await client.delete(
        f"/api/v1/auth/sessions/{uuid4()}", headers=_auth_header(verified_user)
    )

    assert response.status_code == 404


async def test_revoke_session_removes_it_from_active_list(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    await client.post(
        "/api/v1/auth/login", json={"email": verified_user.email, "password": _VALID_PASSWORD}
    )
    headers = _auth_header(verified_user)
    listed = (await client.get("/api/v1/auth/sessions", headers=headers)).json()["data"]
    session_id = listed[0]["id"]

    response = await client.delete(f"/api/v1/auth/sessions/{session_id}", headers=headers)

    assert response.status_code == 200
    remaining = (await client.get("/api/v1/auth/sessions", headers=headers)).json()["data"]
    assert remaining == []


async def test_revoke_session_owned_by_another_user_returns_404(
    client: httpx.AsyncClient, verified_user: User, user_repo: UserRepository, session: AsyncSession
) -> None:
    other_user = await user_repo.create(
        email="bob@example.com",
        hashed_password=hash_password(_VALID_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    await session.commit()
    await client.post(
        "/api/v1/auth/login", json={"email": other_user.email, "password": _VALID_PASSWORD}
    )
    other_sessions = (
        await client.get("/api/v1/auth/sessions", headers=_auth_header(other_user))
    ).json()["data"]
    other_session_id = other_sessions[0]["id"]

    response = await client.delete(
        f"/api/v1/auth/sessions/{other_session_id}", headers=_auth_header(verified_user)
    )

    assert response.status_code == 404


# --- /change-password (T-216) ---------------------------------------------


async def test_change_password_without_bearer_token_returns_401(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": _VALID_PASSWORD, "new_password": "Even-Better-Horse8"},
    )

    assert response.status_code == 401


async def test_change_password_wrong_current_password_returns_401(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    response = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "wrong-password", "new_password": "Even-Better-Horse8"},
        headers=_auth_header(verified_user),
    )

    assert response.status_code == 401


async def test_change_password_happy_path_allows_login_with_new_password(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    response = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": _VALID_PASSWORD, "new_password": "Even-Better-Horse8"},
        headers=_auth_header(verified_user),
    )

    assert response.status_code == 200
    old_login = await client.post(
        "/api/v1/auth/login", json={"email": verified_user.email, "password": _VALID_PASSWORD}
    )
    assert old_login.status_code == 401
    new_login = await client.post(
        "/api/v1/auth/login",
        json={"email": verified_user.email, "password": "Even-Better-Horse8"},
    )
    assert new_login.status_code == 200


async def test_change_password_revokes_other_sessions_but_keeps_current_one(
    client: httpx.AsyncClient, verified_user: User, session_repo: SessionRepository
) -> None:
    # A separate, independent session (e.g. a second device).
    login_elsewhere = await client.post(
        "/api/v1/auth/login", json={"email": verified_user.email, "password": _VALID_PASSWORD}
    )
    assert login_elsewhere.status_code == 200
    client.cookies.clear()

    # This request's own login is what sets the cookie change-password reads
    # to identify "the current session" below -- it must be the one session
    # that survives.
    await client.post(
        "/api/v1/auth/login", json={"email": verified_user.email, "password": _VALID_PASSWORD}
    )

    response = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": _VALID_PASSWORD, "new_password": "Even-Better-Horse8"},
        headers=_auth_header(verified_user),
    )

    assert response.status_code == 200
    active = await session_repo.list_active_for_user(verified_user.id)
    assert len(active) == 1


# --- /change-email / /change-email/confirm (T-218) --------------------------


async def test_change_email_without_bearer_token_returns_401(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/auth/change-email", json={"new_email": "new@example.com"})

    assert response.status_code == 401


async def test_change_email_without_recent_auth_returns_403(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    response = await client.post(
        "/api/v1/auth/change-email",
        json={"new_email": "new@example.com"},
        headers=_stale_auth_header(verified_user),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "step_up_auth_required"


async def test_change_email_happy_path_returns_200(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    response = await client.post(
        "/api/v1/auth/change-email",
        json={"new_email": "new@example.com", "current_password": _VALID_PASSWORD},
        headers=_auth_header(verified_user),
    )

    assert response.status_code == 200


async def test_change_email_wrong_current_password_returns_401(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    # T-226: re-verifying the *old* address's own credential, on top of
    # require_recent_auth's session-freshness check above.
    response = await client.post(
        "/api/v1/auth/change-email",
        json={"new_email": "new@example.com", "current_password": "wrong-password"},
        headers=_auth_header(verified_user),
    )

    assert response.status_code == 401


async def test_confirm_email_change_happy_path_updates_email_and_allows_new_login(
    client: httpx.AsyncClient,
    verified_user: User,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    await client.post(
        "/api/v1/auth/change-email",
        json={"new_email": "new@example.com", "current_password": _VALID_PASSWORD},
        headers=_auth_header(verified_user),
    )
    change_email_jobs = [
        kwargs for name, kwargs in enqueued_jobs if kwargs.get("template") == "change_email"
    ]
    assert len(change_email_jobs) == 1
    confirm_url: str = change_email_jobs[0]["context"]["confirm_url"]
    plain = confirm_url.rsplit("token=", 1)[1]

    response = await client.post("/api/v1/auth/change-email/confirm", json={"token": plain})

    assert response.status_code == 200
    assert response.json()["data"]["email"] == "new@example.com"
    new_login = await client.post(
        "/api/v1/auth/login", json={"email": "new@example.com", "password": _VALID_PASSWORD}
    )
    assert new_login.status_code == 200


async def test_confirm_email_change_invalid_token_returns_401(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/change-email/confirm", json={"token": "not-a-real-token"}
    )

    assert response.status_code == 401


# --- /change-email/cancel (T-226) --------------------------------------------


async def test_cancel_email_change_happy_path_blocks_the_confirm_link(
    client: httpx.AsyncClient,
    verified_user: User,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    await client.post(
        "/api/v1/auth/change-email",
        json={"new_email": "new@example.com", "current_password": _VALID_PASSWORD},
        headers=_auth_header(verified_user),
    )
    change_email_jobs = [
        kwargs for name, kwargs in enqueued_jobs if kwargs.get("template") == "change_email"
    ]
    confirm_plain = change_email_jobs[0]["context"]["confirm_url"].rsplit("token=", 1)[1]
    notifications = [
        kwargs
        for name, kwargs in enqueued_jobs
        if kwargs.get("template") == "security_notification"
        and kwargs["context"]["event"] == "email_change_requested"
    ]
    cancel_plain = notifications[0]["context"]["cancel_url"].rsplit("token=", 1)[1]

    cancel_response = await client.post(
        "/api/v1/auth/change-email/cancel", json={"token": cancel_plain}
    )
    assert cancel_response.status_code == 200

    confirm_response = await client.post(
        "/api/v1/auth/change-email/confirm", json={"token": confirm_plain}
    )
    assert confirm_response.status_code == 401


async def test_cancel_email_change_invalid_token_returns_401(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/change-email/cancel", json={"token": "not-a-real-token"}
    )

    assert response.status_code == 401


# The per-target-address rate limit (AuthService._check_email_change_target_
# rate_limit) isn't re-exercised at the HTTP layer here: these requests are
# all one authenticated user, so sensitive_limit's own 3/minute cap trips on
# the same 4th request whichever distinct target address the test names,
# making the two limits indistinguishable over HTTP.
# test_auth_service_email_change.py's
# test_request_email_change_target_rate_limited exercises it directly instead.
#
# (This used to say the two were indistinguishable because _key_func always
# fell back to the source address, "which it never is in this codebase" --
# true at the time, and the bug: nothing set request.state.user_id, so every
# authenticated caller shared one per-IP bucket. _key_func reads the bearer
# token now. The conclusion here survives the fix because one user is still
# one bucket; the reason it gave does not.)


# --- revocation takes issued access tokens with it ------------------------------------
#
# Repository audit finding 6. Revoking a session revoked only the *refresh*
# half: access tokens already issued from it kept working until they expired,
# so "sign out all devices" was a promise about the next fifteen minutes
# rather than containment of a stolen bearer token. The token now carries its
# session's id, and every revocation path writes `revoked_at`.


async def _login_bearer(client: httpx.AsyncClient, user: User) -> dict[str, str]:
    """A bearer header from a *real* login, so the token carries a `sid`.

    `_auth_header` mints one directly and has no session behind it, which is
    the shape an impersonation token has -- useful elsewhere, useless here.
    """
    response = await client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": _VALID_PASSWORD}
    )
    assert response.status_code == 200, response.text
    client.cookies.clear()
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


async def test_a_bearer_token_stops_working_the_moment_its_session_is_revoked(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    bearer = await _login_bearer(client, verified_user)
    assert (await client.get("/api/v1/auth/me", headers=bearer)).status_code == 200

    assert (await client.post("/api/v1/auth/logout-all", headers=bearer)).status_code == 200

    # The assertion that was previously false: not "after it expires", now.
    assert (await client.get("/api/v1/auth/me", headers=bearer)).status_code == 401


async def test_revoking_one_device_leaves_the_others_signed_in(
    client: httpx.AsyncClient, verified_user: User, session_repo: SessionRepository
) -> None:
    # Each login's session is identified by diffing the active set around it,
    # not by ordering on created_at: two logins in one test land in the same
    # clock tick often enough that "the older row" picks the wrong one, which
    # is how the first version of this test managed to revoke a session and
    # then assert against the token that was still valid.
    before = {s.id for s in await session_repo.list_active_for_user(verified_user.id)}
    first = await _login_bearer(client, verified_user)
    after_first = {s.id for s in await session_repo.list_active_for_user(verified_user.id)}
    first_session = (after_first - before).pop()

    second = await _login_bearer(client, verified_user)
    assert len(await session_repo.list_active_for_user(verified_user.id)) == 2

    # Revoked through the endpoint the security page actually calls, rather
    # than through the repository: `SessionRepository.revoke` only flushes,
    # and each request gets its own session, so a repository-level revoke in
    # the test transaction is invisible to the request under test.
    revoked = await client.delete(f"/api/v1/auth/sessions/{first_session}", headers=second)
    assert revoked.status_code == 200, revoked.text

    assert (await client.get("/api/v1/auth/me", headers=first)).status_code == 401
    assert (await client.get("/api/v1/auth/me", headers=second)).status_code == 200


async def test_a_rotated_token_outlives_the_session_the_rotation_revoked(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    """The regression guard for minting order.

    A refresh revokes the old session and creates its successor. The new
    access token has to name the *successor* -- if it named the session that
    was just revoked, every refresh would hand back a token that was already
    dead, and the whole app would log itself out every fifteen minutes.
    """
    login = await client.post(
        "/api/v1/auth/login", json={"email": verified_user.email, "password": _VALID_PASSWORD}
    )
    assert login.status_code == 200

    refreshed = await client.post("/api/v1/auth/refresh")
    assert refreshed.status_code == 200, refreshed.text
    rotated = {"Authorization": f"Bearer {refreshed.json()['data']['access_token']}"}

    assert (await client.get("/api/v1/auth/me", headers=rotated)).status_code == 200


async def test_a_token_with_no_session_still_resolves(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    """`sid` absent is not a token to reject.

    It is how an impersonation token looks -- T-142 issues one with no
    refresh token, so there is no session to check and the jti blocklist is
    its revocation path -- and how a token minted before the claim existed
    looks while it drains after a deploy.
    """
    response = await client.get("/api/v1/auth/me", headers=_auth_header(verified_user))

    assert response.status_code == 200


# --- Rejection paths whose security consequence must outlive the rejection ---
#
# Each of these raises AuthenticationError *after* writing the state that is
# the whole point of the check (a revocation, a lockout). This file's
# get_session override rolls back on an exception exactly as production does,
# so these are the tests that can tell "written" from "written and kept".
# Results are read through a brand-new session, never the request's.


async def _mobile_login(client: httpx.AsyncClient, user: User) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": _VALID_PASSWORD},
        headers={_PLATFORM_HEADER: "mobile"},
    )
    assert response.status_code == 200, response.text
    client.cookies.clear()
    return response.json()["data"]


async def _mobile_refresh(client: httpx.AsyncClient, refresh_token: str) -> httpx.Response:
    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
        headers={_PLATFORM_HEADER: "mobile"},
    )
    client.cookies.clear()
    return response


async def test_replaying_a_rotated_refresh_token_signs_out_every_device(
    client: httpx.AsyncClient,
    verified_user: User,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    device_a = await _mobile_login(client, verified_user)
    device_b = await _mobile_login(client, verified_user)

    rotated = await _mobile_refresh(client, device_a["refresh_token"])
    assert rotated.status_code == 200, rotated.text
    successor = rotated.json()["data"]

    # The replay: A's pre-rotation token, presented again.
    replay = await _mobile_refresh(client, device_a["refresh_token"])
    assert replay.status_code == 401

    # Nothing the user holds may still work -- not the successor the thief
    # would have, and not the other device either.
    for bearer in (successor["access_token"], device_b["access_token"]):
        me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {bearer}"})
        assert me.status_code == 401
    assert (await _mobile_refresh(client, successor["refresh_token"])).status_code == 401

    async with session_factory() as fresh:
        live = await SessionRepository(fresh).list_active_for_user(verified_user.id)
    assert live == []


async def test_repeated_wrong_passwords_lock_the_account_over_http(
    client: httpx.AsyncClient,
    verified_user: User,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Out of the per-IP limiter's way: this is about the per-account lockout.
    monkeypatch.setenv("AUTH_RATE_LIMIT", "1000/minute")
    get_settings.cache_clear()

    for _ in range(10):
        wrong = await client.post(
            "/api/v1/auth/login",
            json={"email": verified_user.email, "password": "Wrong-Horse9"},
        )
        assert wrong.status_code == 401

    right = await client.post(
        "/api/v1/auth/login",
        json={"email": verified_user.email, "password": _VALID_PASSWORD},
    )
    assert right.status_code == 401
    assert right.json()["error"]["message_key"] == "errors.account_locked"

    async with session_factory() as fresh:
        user = await UserRepository(fresh).get_by_id(verified_user.id)
        assert user is not None
        assert user.locked_until is not None
        actions = (
            (
                await fresh.execute(
                    select(AuditEvent.action).where(
                        col(AuditEvent.resource_id) == str(verified_user.id)
                    )
                )
            )
            .scalars()
            .all()
        )
    assert actions.count("user.login_failed") == 9
    assert actions.count("user.account_locked") == 1


async def test_losing_a_concurrent_refresh_race_signs_out_the_winner_too(
    client: httpx.AsyncClient,
    verified_user: User,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One refresh token, two requests at once, each on its own connection.

    The atomic rotation lets exactly one through. The other cannot tell a
    client racing itself from a thief, so it treats the race as theft and
    must take the winner's fresh session down too -- which only holds if
    that revocation survives the loser's own 401.
    """
    device = await _mobile_login(client, verified_user)

    # Left to the scheduler, the second request tends to read the token after
    # the first has committed, and so takes the plain replay path instead of
    # the race path. Holding both at the rotation until both have read the
    # still-live session makes this test about the race it is named for.
    # (Checked: with the race branch reverted to a bare raise, this fails.)
    rotate = SessionRepository.revoke_if_active
    arrived: list[None] = []
    both_read = asyncio.Event()

    async def _rendezvous(self: SessionRepository, session_id: UUID) -> bool:
        arrived.append(None)
        if len(arrived) == 2:  # the two racing requests
            both_read.set()
        await asyncio.wait_for(both_read.wait(), timeout=10)
        return await rotate(self, session_id)

    monkeypatch.setattr(SessionRepository, "revoke_if_active", _rendezvous)

    first, second = await asyncio.gather(
        _mobile_refresh(client, device["refresh_token"]),
        _mobile_refresh(client, device["refresh_token"]),
    )

    assert sorted([first.status_code, second.status_code]) == [200, 401]
    winner = first if first.status_code == 200 else second
    successor = winner.json()["data"]
    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {successor['access_token']}"}
    )
    assert me.status_code == 401
    assert (await _mobile_refresh(client, successor["refresh_token"])).status_code == 401

    async with session_factory() as fresh:
        assert await SessionRepository(fresh).list_active_for_user(verified_user.id) == []


async def test_a_signed_out_device_trying_its_old_token_does_not_sign_out_the_others(
    client: httpx.AsyncClient, verified_user: User
) -> None:
    """Only a *rotated* token coming back is a theft signal.

    A device that was signed out -- by its own logout, a password change, a
    2FA enrollment elsewhere -- will present its refresh token once more;
    that is what it does next, not an attack. Once R2 made the theft
    response actually persist, treating every revoked token as stolen meant
    that first stale refresh signed the user out of every other device too.
    """
    device_a = await _mobile_login(client, verified_user)
    device_b = await _mobile_login(client, verified_user)

    logout = await client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": device_a["refresh_token"]},
        headers={_PLATFORM_HEADER: "mobile"},
    )
    assert logout.status_code == 200, logout.text
    client.cookies.clear()

    assert (await _mobile_refresh(client, device_a["refresh_token"])).status_code == 401

    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {device_b['access_token']}"}
    )
    assert me.status_code == 200
    assert (await _mobile_refresh(client, device_b["refresh_token"])).status_code == 200
