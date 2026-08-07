"""
T-063: GET /api/v1/config is the one unauthenticated endpoint every frontend
surface uses to learn which modules are active and how the product is
branded -- see packages/hooks/src/useModules.ts. Uses the shared client/
session fixtures from conftest.py (T-048) rather than rolling its own
testcontainers setup: this route touches no DB session of its own, so it
needs nothing beyond a working app + real Redis for the rate-limit middleware
already wired into `client`.
"""

from collections.abc import Generator

import httpx
import pytest

from app.core import module_loader
from app.core.i18n import get_supported_locales
from app.core.locales import EnabledLocales, LocaleConfig
from app.core.modules import EnabledModules, ModuleConfig
from product import config as product_config


@pytest.fixture(autouse=True)
def _clear_caches() -> Generator[None]:
    module_loader.get_modules.cache_clear()
    module_loader.get_branding.cache_clear()
    module_loader.get_support_config.cache_clear()
    yield
    module_loader.get_modules.cache_clear()
    module_loader.get_branding.cache_clear()
    module_loader.get_support_config.cache_clear()


async def test_returns_branding_and_default_module_config(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/v1/config")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["app_name"] == product_config.APP_NAME
    assert data["primary_color"] == product_config.PRIMARY_COLOR
    assert data["logo_url"] == product_config.LOGO_URL
    assert data["support_email"] == product_config.SUPPORT_EMAIL
    assert data["modules"] == {
        "billing": True,
        "notifications": True,
        "file_storage": True,
        "feature_flags": True,
        "gdpr": True,
        "admin": True,
        "oauth": True,
        "totp_2fa": True,
        "multi_org": True,
        "usage_metering": False,
        "outbound_webhooks": False,
    }
    assert data["supported_locales"] == get_supported_locales()
    assert data["default_locale"] == product_config.DEFAULT_LOCALE


async def test_reflects_disabled_modules(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        product_config,
        "MODULES",
        EnabledModules(
            billing=ModuleConfig(enabled=False),
            notifications=ModuleConfig(enabled=False),
            file_storage=ModuleConfig(enabled=False),
        ),
    )

    response = await client.get("/api/v1/config")

    data = response.json()["data"]
    assert data["modules"]["billing"] is False
    assert data["modules"]["notifications"] is False
    assert data["modules"]["file_storage"] is False
    assert data["modules"]["gdpr"] is True


async def test_reflects_disabled_locales(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        product_config,
        "LOCALES",
        EnabledLocales(
            en=LocaleConfig(enabled=True),
            fi=LocaleConfig(enabled=False),
            de=LocaleConfig(enabled=False),
            es=LocaleConfig(enabled=False),
            fr=LocaleConfig(enabled=False),
            ar=LocaleConfig(enabled=False),
        ),
    )

    response = await client.get("/api/v1/config")

    assert response.json()["data"]["supported_locales"] == ["en"]


async def test_reports_the_support_tool_and_a_resolved_support_url(
    client: httpx.AsyncClient,
) -> None:
    """T-262: SUPPORT_URL is unset by default, so the URL every frontend
    falls back to is the mailto: form of SUPPORT_EMAIL -- resolved here, not
    in each of web and mobile."""
    response = await client.get("/api/v1/config")

    data = response.json()["data"]
    assert data["support_tool"] == product_config.SUPPORT_TOOL
    assert data["support_url"] == f"mailto:{product_config.SUPPORT_EMAIL}"


async def test_configured_support_url_wins_over_the_mailto_fallback(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(product_config, "SUPPORT_TOOL", "intercom")
    monkeypatch.setattr(product_config, "SUPPORT_URL", "https://help.example.com")

    response = await client.get("/api/v1/config")

    data = response.json()["data"]
    assert data["support_tool"] == "intercom"
    assert data["support_url"] == "https://help.example.com"


async def test_accessible_without_auth_token(client: httpx.AsyncClient) -> None:
    assert "Authorization" not in client.headers

    response = await client.get("/api/v1/config")

    assert response.status_code == 200


async def test_cache_control_header_present(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/config")

    assert response.headers["cache-control"] == "public, max-age=300"
