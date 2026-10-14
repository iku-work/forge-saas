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
from typing import get_args

import httpx
import pytest

from app.api.v1.config import ModulesResponse
from app.core import module_loader
from app.core.config import get_settings
from app.core.i18n import get_supported_locales
from app.core.locales import EnabledLocales, LocaleConfig
from app.core.modules import EnabledModules, ModuleConfig, ModuleName
from app.modules.product_settings import service as product_settings
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


# Asserts the committed defaults verbatim; test_reflects_disabled_modules
# below is the same endpoint read from whatever config is active.
@pytest.mark.module_dependent
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
        "marketing": True,
        "public_api": False,
        "enterprise_sso": False,
    }
    assert data["supported_locales"] == get_supported_locales()
    assert data["default_locale"] == product_config.DEFAULT_LOCALE


# --- UX-01: which social providers actually exist -------------------------
#
# The login page rendered "Continue with Google" and "Continue with GitHub"
# whenever the oauth *module* was on. Neither provider is configured on a
# fresh deployment of this template, so both buttons led to a 404 -- the
# module flag says the routes exist, which is a different question from
# whether any provider will answer.


async def test_reports_no_oauth_providers_when_none_are_configured(
    client: httpx.AsyncClient,
) -> None:
    """The default, and the state the finding was reported against: the
    module is on and there are no client IDs anywhere."""
    response = await client.get("/api/v1/config")

    assert response.json()["data"]["oauth_providers"] == []


async def test_reports_only_the_providers_that_have_credentials(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Half-configured is the common case -- a product registers a Google
    app and never gets round to GitHub."""
    monkeypatch.setattr(product_config, "MODULES", EnabledModules())  # oauth on
    settings = get_settings()
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "google-client-id")
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "google-secret")

    response = await client.get("/api/v1/config")

    assert response.json()["data"]["oauth_providers"] == ["google"]


async def test_a_provider_with_only_half_its_credentials_is_not_offered(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ID with no secret cannot complete an exchange, so advertising it
    would reproduce the finding in a subtler form."""
    settings = get_settings()
    monkeypatch.setattr(settings, "GITHUB_CLIENT_ID", "github-client-id")
    monkeypatch.setattr(settings, "GITHUB_CLIENT_SECRET", "")

    response = await client.get("/api/v1/config")

    assert response.json()["data"]["oauth_providers"] == []


# --- UX-02: where the legal pages live ------------------------------------
#
# The terms document asserts that creating an account is acceptance of it,
# and the registration screen linked to neither it nor the privacy policy.
# They are served by the marketing app on its own host (T-307), so the
# frontend cannot build a relative link or guess the hostname -- it has to
# be told.


async def test_reports_the_marketing_origin(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(product_config, "MODULES", EnabledModules())  # marketing on
    settings = get_settings()
    monkeypatch.setattr(settings, "MARKETING_DOMAIN", "www.example.com")

    response = await client.get("/api/v1/config")

    # https, not http: Caddy terminates TLS for this host in every
    # environment, dev included.
    assert response.json()["data"]["marketing_url"] == "https://www.example.com"


async def test_the_marketing_url_is_empty_when_the_module_is_off(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """There is no terms page and no privacy page in that product, so the
    app must render no link rather than one that 404s."""
    monkeypatch.setattr(
        product_config,
        "MODULES",
        EnabledModules(marketing=ModuleConfig(enabled=False)),
    )

    response = await client.get("/api/v1/config")

    assert response.json()["data"]["marketing_url"] == ""


async def test_disabling_the_module_empties_the_list(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Configured credentials must not leak through a disabled module --
    the routes are not registered, so a button would 404 again.

    This is why clients can render from `oauth_providers` alone without
    also consulting `modules.oauth`.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "google-client-id")
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "google-secret")
    monkeypatch.setattr(
        product_config,
        "MODULES",
        EnabledModules(oauth=ModuleConfig(enabled=False)),
    )

    response = await client.get("/api/v1/config")

    body = response.json()["data"]
    assert body["modules"]["oauth"] is False
    assert body["oauth_providers"] == []


def test_modules_response_covers_every_registered_module() -> None:
    # T-290. This response is the only way web and mobile learn which modules
    # are on, so a module registered in EnabledModules but missing here is a
    # switch no frontend can ever read -- and a field here with no module
    # behind it is a lie the client would happily gate UI on. Set equality
    # catches both directions, which is the point: the next module to be added
    # cannot be added to one of these and forgotten in the other.
    assert set(ModulesResponse.model_fields) == set(get_args(ModuleName))


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


async def test_cache_control_matches_the_settings_ttl(client: httpx.AsyncClient) -> None:
    """T-263 brought this down from 300s. Nothing can invalidate a browser's
    own HTTP cache, so this max-age *is* the worst case an operator waits for
    a banner or a maintenance flag to reach an already-open tab -- it is
    pinned to the settings cache TTL rather than left five times longer."""
    response = await client.get("/api/v1/config")

    assert response.headers["cache-control"] == (f"public, max-age={product_settings.TTL_SECONDS}")


async def test_reports_no_banner_and_no_maintenance_by_default(
    client: httpx.AsyncClient,
) -> None:
    """T-263: the runtime half of this response, with nothing flipped. The
    populated cases live in tests/test_maintenance_mode.py and
    tests/test_admin_product_settings_router.py, which can actually set a
    value first."""
    data = (await client.get("/api/v1/config")).json()["data"]

    assert data["banner"] is None
    assert data["maintenance"] == {"enabled": False, "message": ""}
    assert data["signup_disabled"] is False
