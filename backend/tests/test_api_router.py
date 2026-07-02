"""
T-061: build_router() only includes an optional module's router when that
module is enabled in product/config.py -- a disabled module's routes don't
exist at all (404), rather than existing but rejecting requests.

Same monkeypatch-product_config.MODULES + clear-get_modules-cache pattern as
test_module_loader.py, since build_router() reads the lru_cache'd
get_modules() directly. Presence/absence of a route is read off a throwaway
FastAPI app's OpenAPI schema rather than walking router.routes directly --
this FastAPI version defers prefix-combination until the app resolves routes
(include_router() wraps sub-routers in a lazy `_IncludedRouter` instead of
eagerly flattening), but schema generation always fully resolves paths. No
Postgres/Redis containers needed: the 404 case is returned by FastAPI's
routing layer before any dependency or handler runs. Billing-enabled 200
behavior is already covered end-to-end by test_billing_router.py against the
default (all-enabled) config.
"""

from collections.abc import Generator
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from app.api.v1 import build_router
from app.core import module_loader
from app.core.modules import EnabledModules, ModuleConfig
from product import config as product_config

# notifications/file_storage both `requires=["billing"]` (T-060) -- disabling
# billing alone trips validate_modules, so every "billing off" config here
# disables its dependents too.
_BILLING_OFF = EnabledModules(
    billing=ModuleConfig(enabled=False),
    notifications=ModuleConfig(enabled=False),
    file_storage=ModuleConfig(enabled=False),
)


@pytest.fixture(autouse=True)
def _clear_modules_cache() -> Generator[None]:
    module_loader.get_modules.cache_clear()
    yield
    module_loader.get_modules.cache_clear()


def _paths() -> set[str]:
    app = FastAPI()
    app.include_router(build_router())
    return set(app.openapi()["paths"])


def test_billing_routes_present_when_enabled() -> None:
    assert "/api/v1/billing/plans" in _paths()


def test_config_route_always_present() -> None:
    assert "/api/v1/config" in _paths()


def test_billing_routes_absent_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(product_config, "MODULES", _BILLING_OFF)

    paths = _paths()

    assert not any(path.startswith("/api/v1/billing") for path in paths)


def test_gdpr_routes_absent_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(product_config, "MODULES", EnabledModules(gdpr=ModuleConfig(enabled=False)))

    paths = _paths()

    assert "/api/v1/me/export" not in paths
    assert "/api/v1/me/consents" not in paths
    # /api/v1/me itself (DELETE) is gdpr's, but /api/v1/me/notification-
    # preferences (T-103) belongs to the unrelated notifications module --
    # a blanket "/api/v1/me" prefix check would wrongly fail once that route
    # exists, since gdpr being disabled here doesn't disable notifications.
    assert "/api/v1/me" not in paths
    assert "/api/v1/me/notification-preferences" in paths


def test_notifications_routes_present_when_enabled() -> None:
    paths = _paths()

    assert "/api/v1/notifications" in paths
    assert "/api/v1/notifications/stream" in paths
    assert "/api/v1/me/notification-preferences" in paths


def test_notifications_routes_absent_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(product_config, "MODULES", _BILLING_OFF)

    paths = _paths()

    assert not any(path.startswith("/api/v1/notifications") for path in paths)
    assert "/api/v1/me/notification-preferences" not in paths


def test_always_on_routes_survive_every_optional_module_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        product_config,
        "MODULES",
        EnabledModules(
            billing=ModuleConfig(enabled=False),
            notifications=ModuleConfig(enabled=False),
            file_storage=ModuleConfig(enabled=False),
            gdpr=ModuleConfig(enabled=False),
        ),
    )

    paths = _paths()

    assert "/api/v1/auth/login" in paths
    assert "/api/v1/orgs" in paths
    assert "/api/v1/config" in paths


def test_admin_routes_excluded_from_openapi_schema() -> None:
    # T-140: include_in_schema=False on the admin router -- this app builds
    # one process-wide cached OpenAPI schema (no per-token variance), so
    # never showing admin routes to anyone trivially satisfies "not shown to
    # non-admin tokens".
    paths = _paths()

    assert not any(path.startswith("/api/v1/admin") for path in paths)


async def test_admin_routes_absent_when_module_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        product_config, "MODULES", EnabledModules(admin=ModuleConfig(enabled=False))
    )
    app = FastAPI()
    app.include_router(build_router())

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
        response = await client.get(f"/api/v1/admin/orgs/{uuid4()}/feature-flags")

    assert response.status_code == 404


async def test_disabled_billing_route_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(product_config, "MODULES", _BILLING_OFF)
    app = FastAPI()
    app.include_router(build_router())

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as client:
        response = await client.get("/api/v1/billing/plans")

    assert response.status_code == 404
