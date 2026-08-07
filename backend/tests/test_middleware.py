"""
Two client styles here, deliberately, split on whether the request has to
reach a *route handler*:

`TestClient(app)` where the assertion is about middleware alone -- /health,
an unmatched path, a CORS preflight, and the 415 rejection (which the
content-type middleware answers before routing). None of those need
Postgres, Redis, or the rate limiter.

conftest's `client` fixture wherever the request must actually be handled by
a route under /api/v1. Those carry `@api_limit`/`@auth_limit`, and slowapi's
limiter is constructed at *import* time from `settings.REDIS_URL` -- see
app/middleware/rate_limit.py's own comment about the same import-time trap
for limit values, which was fixed there but not for the storage URI. A bare
TestClient therefore sends them at whatever Redis `backend/.env` names
(`localhost:6379`, which only exists on a machine running the compose stack)
rather than the throwaway container conftest's `_rate_limit_storage` points
the limiter at. That is what made these tests fail on any checkout whose
`.env` wasn't a copy of `.env.test`, while passing in CI, which copies it.
"""

from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.main import settings as app_settings
from app.middleware.security_headers import SECURITY_HEADERS
from app.modules.product_settings import service as product_settings


@pytest.mark.owasp("A02")
@pytest.mark.owasp("A05")
def test_security_headers_present_on_response() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    for header, value in SECURITY_HEADERS.items():
        assert response.headers[header] == value


def test_cache_control_defaults_to_no_store() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.headers["Cache-Control"] == "no-store"


def test_cache_control_no_store_applied_to_404s_too() -> None:
    # T-210: ZAP's baseline scan flagged unmatched routes (its spider probes
    # "/", "/robots.txt", "/sitemap.xml") as storable/cacheable -- the
    # middleware must set the default on every response, not just 2xx ones.
    with TestClient(app) as client:
        response = client.get("/this-route-does-not-exist")

    assert response.status_code == 404
    assert response.headers["Cache-Control"] == "no-store"


async def test_cache_control_not_overridden_when_handler_sets_its_own(
    client: httpx.AsyncClient,
) -> None:
    # api/v1/config (T-063) deliberately sets a public, cacheable
    # Cache-Control -- the security-headers default must not clobber it.
    # T-263 shortened the max-age from 300s to the settings-cache TTL (the
    # response now carries operator-flippable state); this asserts against
    # that constant rather than a literal so the two cannot drift apart.
    response = await client.get("/api/v1/config")

    assert response.headers["Cache-Control"] == (f"public, max-age={product_settings.TTL_SECONDS}")


def test_request_id_echoed_when_provided() -> None:
    with TestClient(app) as client:
        response = client.get("/health", headers={"X-Request-ID": "client-supplied-id"})

    assert response.headers["X-Request-ID"] == "client-supplied-id"


def test_request_id_generated_when_absent() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert UUID(response.headers["X-Request-ID"])


def test_cors_allows_configured_origin() -> None:
    # Asserted against the origin the *app object* was built with, not a
    # literal. CORSMiddleware takes allow_origins by value at
    # app.add_middleware() time -- i.e. when app.main is imported, before
    # conftest's autouse _settings_env fixture has applied .env.test -- so a
    # hardcoded "http://localhost:5173" only matched on a machine whose
    # backend/.env happened to be a copy of .env.test (as CI's is). The
    # negative case below still pins a literal, since no env file may ever
    # allow it.
    assert app_settings.ALLOWED_ORIGINS, (
        "ALLOWED_ORIGINS is empty -- the app was imported with an env file "
        "that configures no CORS origin at all, so there is nothing to allow."
    )
    origin = app_settings.ALLOWED_ORIGINS[0]

    with TestClient(app) as client:
        response = client.get("/health", headers={"Origin": origin})

    assert response.headers["access-control-allow-origin"] == origin


@pytest.mark.owasp("A05")
def test_cors_rejects_unconfigured_origin() -> None:
    with TestClient(app) as client:
        response = client.get("/health", headers={"Origin": "https://evil.example.com"})

    assert "access-control-allow-origin" not in response.headers


@pytest.mark.owasp("A05")
def test_wrong_content_type_on_post_with_body_returns_415() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/login", content=b"not json", headers={"Content-Type": "text/plain"}
        )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_media_type"


def test_missing_content_type_on_post_with_body_returns_415() -> None:
    with TestClient(app) as client:
        response = client.post("/api/v1/auth/login", content=b'{"email": "a@example.com"}')

    assert response.status_code == 415


async def test_json_content_type_on_post_is_not_rejected(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "wrong"},
    )

    # Reaches the route handler and fails there, on bad credentials -- proves
    # the 415 check isn't firing on a legitimate JSON request. Asserted as
    # the specific 401 rather than "not 415": under a bare TestClient this
    # request never got past the rate limiter, and "not 415" was satisfied by
    # the resulting 500 just as happily as by reaching the handler.
    assert response.status_code == 401


async def test_post_with_no_body_is_not_rejected_for_content_type(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/api/v1/auth/logout")

    assert response.status_code != 415


async def test_content_disposition_attachment_on_api_response(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/config")

    assert response.headers["Content-Disposition"] == "attachment"


def test_content_disposition_not_forced_outside_api_prefix() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert "Content-Disposition" not in response.headers


def test_cors_preflight_allows_authorization_and_org_header() -> None:
    with TestClient(app) as client:
        response = client.options(
            "/api/v1/orgs",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization, x-org-id",
            },
        )

    allowed = response.headers["access-control-allow-headers"]
    assert "authorization" in allowed.lower()
    assert "x-org-id" in allowed.lower()
