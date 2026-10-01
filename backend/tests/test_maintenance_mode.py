"""
T-263, the middleware half: what maintenance mode actually does to traffic.

The point of these tests is the *exception list*. Getting maintenance mode to
503 everything is easy; the failure modes that matter are the ones where an
operator can't turn it back off, where the orchestrator kills the container
because /health went dark, or where the frontend can't tell a maintenance
window apart from a network outage.

Requests go through conftest's shared `client` fixture, which redirects both
of the middleware's own data sources (app.state.redis and the DB fallback) at
the test's containers -- see that fixture's comment. So the middleware under
test here is the real one, in its real position in the stack.
"""

from uuid import uuid4

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache, cache_keys
from app.main import app, check_database_ready, check_redis_ready
from app.middleware import maintenance
from app.middleware.maintenance import MAINTENANCE_CODE
from app.modules.auth.permissions import MemberRole
from app.modules.auth.repository import UserRepository
from app.modules.auth.utils import hash_password
from app.modules.product_settings import keys
from app.modules.product_settings.repository import ProductSettingsRepository
from app.modules.tenancy.repository import MembershipRepository, OrganizationRepository
from tests._auth import session_token

_PASSWORD = "Correct-Horse9"


async def _store(session: AsyncSession, redis: Redis, settings: dict[str, object]) -> None:
    """Write settings the way an operator's PATCH would, minus the HTTP: store
    the row and bust the cache. Used instead of the admin endpoint so these
    tests fail for one reason (the middleware) rather than two."""
    await ProductSettingsRepository(session).replace(dict(settings), uuid4())
    await session.commit()
    await cache.invalidate(redis, cache_keys.product_settings_key())


@pytest.fixture
async def superadmin_client(client: httpx.AsyncClient, session: AsyncSession) -> httpx.AsyncClient:
    # T-218: require_superadmin also gates on a second factor being enrolled.
    user = await UserRepository(session).create(
        email=f"admin-{uuid4()}@example.com",
        hashed_password=hash_password(_PASSWORD),
        is_superadmin=True,
        totp_enabled=True,
    )
    org = await OrganizationRepository(session).create(name="Ops", slug=f"ops-{uuid4().hex[:8]}")
    await MembershipRepository(session).create(
        user_id=user.id, org_id=org.id, role=MemberRole.OWNER
    )
    await session.commit()
    client.headers["Authorization"] = f"Bearer {await session_token(session, user)}"
    client.headers["X-Org-ID"] = str(org.id)
    return client


# ---- off by default ----------------------------------------------------------


async def test_traffic_is_untouched_when_the_switch_is_off(
    authed_client: httpx.AsyncClient,
) -> None:
    assert (await authed_client.get("/api/v1/auth/me")).status_code == 200


# ---- on ----------------------------------------------------------------------


async def test_user_endpoints_return_503_with_the_operators_message(
    authed_client: httpx.AsyncClient, session: AsyncSession, redis: Redis
) -> None:
    await _store(
        session,
        redis,
        {keys.MAINTENANCE_MODE: True, keys.MAINTENANCE_MESSAGE: "Upgrading the database."},
    )

    response = await authed_client.get("/api/v1/auth/me")

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == MAINTENANCE_CODE
    assert error["message"] == "Upgrading the database."
    # Advisory, but the difference between a mobile client retrying in a
    # minute and one retrying in a tight loop for the whole window.
    assert response.headers["retry-after"] == "60"


async def test_the_503_still_carries_a_request_id(
    authed_client: httpx.AsyncClient, session: AsyncSession, redis: Redis
) -> None:
    """Proof the middleware sits *inside* RequestIDMiddleware: a maintenance
    503 an operator can't correlate with a log line is a support ticket."""
    await _store(session, redis, {keys.MAINTENANCE_MODE: True})

    response = await authed_client.get("/api/v1/auth/me")

    assert response.json()["meta"]["request_id"]


async def test_the_503_carries_the_security_headers(
    authed_client: httpx.AsyncClient, session: AsyncSession, redis: Redis
) -> None:
    """And proof it sits inside SecurityHeadersMiddleware too -- T-225's
    headers must not have a hole in them for the duration of a maintenance
    window."""
    await _store(session, redis, {keys.MAINTENANCE_MODE: True})

    response = await authed_client.get("/api/v1/auth/me")

    assert response.headers["x-content-type-options"] == "nosniff"


async def test_a_message_the_operator_never_wrote_falls_back_to_a_translated_one(
    authed_client: httpx.AsyncClient, session: AsyncSession, redis: Redis
) -> None:
    await _store(session, redis, {keys.MAINTENANCE_MODE: True})

    response = await authed_client.get("/api/v1/auth/me")

    error = response.json()["error"]
    assert error["message_key"] == "errors.maintenance"
    assert error["message"]  # the errors.maintenance catalog entry, not ""


async def test_the_message_is_rendered_in_the_callers_locale(
    authed_client: httpx.AsyncClient, session: AsyncSession, redis: Redis
) -> None:
    """LocaleMiddleware runs before this one, which is the other half of why
    the registration order in app.main matters."""
    await _store(session, redis, {keys.MAINTENANCE_MODE: True})

    english = await authed_client.get("/api/v1/auth/me", headers={"Accept-Language": "en"})
    german = await authed_client.get("/api/v1/auth/me", headers={"Accept-Language": "de"})

    assert english.json()["error"]["message"] != german.json()["error"]["message"]


# ---- the exception list ------------------------------------------------------


@pytest.mark.parametrize("path", ["/health", "/health/live"])
async def test_health_probes_stay_reachable(
    client: httpx.AsyncClient, session: AsyncSession, redis: Redis, path: str
) -> None:
    """A maintenance mode that fails liveness gets the container restarted in
    a loop, which is not maintenance."""
    await _store(session, redis, {keys.MAINTENANCE_MODE: True})

    assert (await client.get(path)).status_code == 200


async def test_the_readiness_probe_still_answers_for_itself(
    client: httpx.AsyncClient, session: AsyncSession, redis: Redis
) -> None:
    """/health/ready has to reach its own dependency checks rather than being
    short-circuited: an orchestrator reading a maintenance 503 as "not ready"
    pulls the pod out of rotation, and the operator loses the surface they
    were about to use to end the window.

    Its two probes are overridden the same way tests/test_health.py does it --
    they talk to app.state.engine, which in this fixture is not the test
    container. The 200 is therefore proof the *probe* answered, not the
    middleware.
    """
    await _store(session, redis, {keys.MAINTENANCE_MODE: True})
    app.dependency_overrides[check_database_ready] = lambda: True
    app.dependency_overrides[check_redis_ready] = lambda: True
    try:
        response = await client.get("/health/ready")
    finally:
        app.dependency_overrides.pop(check_database_ready, None)
        app.dependency_overrides.pop(check_redis_ready, None)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_config_stays_reachable_and_reports_the_window(
    client: httpx.AsyncClient, session: AsyncSession, redis: Redis
) -> None:
    """The endpoint every frontend uses to tell "we are in a maintenance
    window" apart from "the network is down"."""
    await _store(
        session, redis, {keys.MAINTENANCE_MODE: True, keys.MAINTENANCE_MESSAGE: "Back at 03:00 UTC"}
    )

    response = await client.get("/api/v1/config")

    assert response.status_code == 200
    assert response.json()["data"]["maintenance"] == {
        "enabled": True,
        "message": "Back at 03:00 UTC",
    }


# The three tests marked below reach for an admin route or the billing kill
# switch; the middleware itself, which is what this file is about, is core.
@pytest.mark.module_dependent
async def test_admin_endpoints_stay_reachable_so_the_window_can_be_ended(
    superadmin_client: httpx.AsyncClient, session: AsyncSession, redis: Redis
) -> None:
    await _store(session, redis, {keys.MAINTENANCE_MODE: True})

    response = await superadmin_client.get("/api/v1/admin/settings/product")

    assert response.status_code == 200
    assert response.json()["data"]["settings"][keys.MAINTENANCE_MODE] is True


@pytest.mark.module_dependent
async def test_admin_endpoints_are_exempt_from_the_switch_not_from_authorization(
    authed_client: httpx.AsyncClient, session: AsyncSession, redis: Redis
) -> None:
    """Exempting /api/v1/admin grants nobody anything -- require_superadmin is
    still in front of it, and an ordinary user gets the same 403 they would
    outside a maintenance window."""
    await _store(session, redis, {keys.MAINTENANCE_MODE: True})

    response = await authed_client.get("/api/v1/admin/settings/product")

    assert response.status_code == 403


async def test_login_stays_reachable(
    client: httpx.AsyncClient, session: AsyncSession, redis: Redis
) -> None:
    """ "Turn maintenance off" must not depend on the operator having happened
    to be signed in before it went on."""
    await UserRepository(session).create(
        email="op@example.com",
        hashed_password=hash_password(_PASSWORD),
        is_superadmin=True,
        totp_enabled=False,
        email_verified_at=None,
    )
    await session.commit()
    await _store(session, redis, {keys.MAINTENANCE_MODE: True})

    response = await client.post(
        "/api/v1/auth/login", json={"email": "op@example.com", "password": _PASSWORD}
    )

    # Whatever login decides (this account is unverified, so it refuses), the
    # decision is login's -- the point is that the middleware let it through
    # rather than answering 503 on its behalf.
    assert response.status_code != 503


async def test_the_openapi_schema_is_reachable_without_a_lifespan(
    client: httpx.AsyncClient, session: AsyncSession, redis: Redis
) -> None:
    """T-050's schemathesis discovery fetches /openapi.json by mounting the
    ASGI app directly, without starting its lifespan -- so `app.state.redis`
    doesn't exist yet. The exemption is what keeps that from being a 500 on
    the one request that has to work before anything else does."""
    await _store(session, redis, {keys.MAINTENANCE_MODE: True})

    assert (await client.get("/openapi.json")).status_code == 200


async def test_cors_preflight_is_not_blocked(
    client: httpx.AsyncClient, session: AsyncSession, redis: Redis
) -> None:
    """A blocked preflight means the browser never sends the real request, so
    the app shows a CORS error instead of the maintenance copy."""
    await _store(session, redis, {keys.MAINTENANCE_MODE: True})

    response = await client.request(
        "OPTIONS",
        "/api/v1/auth/me",
        headers={
            "Origin": "https://app.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code != 503


async def test_a_path_that_merely_starts_with_an_exempt_prefix_is_still_blocked(
    authed_client: httpx.AsyncClient, session: AsyncSession, redis: Redis
) -> None:
    """`/api/v1/config` is exempt; a hypothetical `/api/v1/configuration` must
    not inherit that exemption by string prefix."""
    await _store(session, redis, {keys.MAINTENANCE_MODE: True})

    response = await authed_client.get("/api/v1/configuration")

    assert response.status_code == 503


# ---- kill switches -----------------------------------------------------------


async def test_a_module_kill_switch_503s_only_that_modules_routes(
    authed_client: httpx.AsyncClient, session: AsyncSession, redis: Redis
) -> None:
    await _store(session, redis, {keys.EMERGENCY_KILL_SWITCH: {"billing": True}})

    killed = await authed_client.get("/api/v1/billing/plans")
    untouched = await authed_client.get("/api/v1/auth/me")

    assert killed.status_code == 503
    assert killed.json()["error"]["details"] == {"module": "billing"}
    assert untouched.status_code == 200


@pytest.mark.module_dependent
async def test_a_kill_switch_set_to_false_changes_nothing(
    authed_client: httpx.AsyncClient, session: AsyncSession, redis: Redis
) -> None:
    await _store(session, redis, {keys.EMERGENCY_KILL_SWITCH: {"billing": False}})

    assert (await authed_client.get("/api/v1/billing/plans")).status_code == 200


# ---- signup_disabled ---------------------------------------------------------


async def test_signup_disabled_closes_registration_only(
    client: httpx.AsyncClient, session: AsyncSession, redis: Redis
) -> None:
    await _store(session, redis, {keys.SIGNUP_DISABLED: True})

    response = await client.post(
        "/api/v1/auth/register",
        json={"email": f"new-{uuid4()}@example.com", "password": _PASSWORD, "full_name": "New"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "signup_disabled"
    # Sign-in is untouched: closing public registration must not lock out the
    # customers who already have accounts.
    login = await client.post("/api/v1/auth/login", json={"email": "x@y.z", "password": "nope"})
    assert login.status_code != 403


# ---- degradation -------------------------------------------------------------


async def test_an_unreadable_settings_store_fails_open(
    authed_client: httpx.AsyncClient,
    session: AsyncSession,
    redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The one judgement call in this middleware, pinned by a test.

    With both Redis and the database unreachable, the middleware cannot know
    whether maintenance is on -- and answering 503 to everything on that basis
    turns a degraded system into a fully dead one, at exactly the moment
    somebody is trying to diagnose it. It lets traffic through and says so in
    the logs instead.
    """
    # Maintenance mode really is on, so a middleware that failed *closed*
    # would be indistinguishable from one that simply read the row.
    await _store(session, redis, {keys.MAINTENANCE_MODE: True})

    async def _boom(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise ConnectionError("redis and postgres are both gone")

    monkeypatch.setattr(maintenance, "read_settings", _boom)

    response = await authed_client.get("/api/v1/auth/me")

    assert response.status_code == 200
    # capsys, not caplog: this codebase logs through loguru, whose sink writes
    # to stderr and does not propagate to stdlib logging -- same reason
    # tests/test_logging.py reads capsys. A silent fail-open would be far worse
    # than a loud one, so the log line is part of the contract.
    assert "maintenance.settings_unreadable" in capsys.readouterr().err


async def test_a_request_arriving_before_the_lifespan_ran_fails_open(
    authed_client: httpx.AsyncClient, session: AsyncSession, redis: Redis
) -> None:
    """`app.state.redis` is set by the lifespan. Tooling that mounts the ASGI
    app without one (see the /openapi.json case above) must not get a 500 out
    of an AttributeError."""
    await _store(session, redis, {keys.MAINTENANCE_MODE: True})
    saved = app.state.redis
    del app.state.redis
    try:
        response = await authed_client.get("/api/v1/auth/me")
    finally:
        app.state.redis = saved

    assert response.status_code == 200


async def test_a_missing_translation_still_yields_a_503_not_a_500(
    authed_client: httpx.AsyncClient,
    session: AsyncSession,
    redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`t()` raises KeyError for a key that isn't in the catalog, and this
    middleware calls it while building a response the app.core.handlers
    machinery can no longer catch (it lives inside this middleware). A catalog
    that loses `errors.maintenance` must degrade to English, not turn every
    request in the window into a 500."""

    def _missing(*_args: object, **_kwargs: object) -> str:
        raise KeyError("errors.maintenance")

    monkeypatch.setattr(maintenance, "t", _missing)
    await _store(session, redis, {keys.MAINTENANCE_MODE: True})

    response = await authed_client.get("/api/v1/auth/me")

    assert response.status_code == 503
    assert response.json()["error"]["message"] == (
        "The service is temporarily down for maintenance. Please try again soon."
    )
