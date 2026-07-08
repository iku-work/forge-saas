from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.middleware.security_headers import SECURITY_HEADERS


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


def test_cache_control_not_overridden_when_handler_sets_its_own() -> None:
    # api/v1/config (T-063) deliberately sets a long-lived public
    # Cache-Control -- the security-headers default must not clobber it.
    with TestClient(app) as client:
        response = client.get("/api/v1/config")

    assert response.headers["Cache-Control"] == "public, max-age=300"


def test_request_id_echoed_when_provided() -> None:
    with TestClient(app) as client:
        response = client.get("/health", headers={"X-Request-ID": "client-supplied-id"})

    assert response.headers["X-Request-ID"] == "client-supplied-id"


def test_request_id_generated_when_absent() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert UUID(response.headers["X-Request-ID"])


def test_cors_allows_configured_origin() -> None:
    with TestClient(app) as client:
        response = client.get("/health", headers={"Origin": "http://localhost:5173"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


@pytest.mark.owasp("A05")
def test_cors_rejects_unconfigured_origin() -> None:
    with TestClient(app) as client:
        response = client.get("/health", headers={"Origin": "https://evil.example.com"})

    assert "access-control-allow-origin" not in response.headers


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
