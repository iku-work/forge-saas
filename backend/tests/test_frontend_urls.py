"""
T-233: app/core/urls.py — the builder every transactional email uses for
links back into the web app, now that the web app carries the active locale
as the first path segment (`/fi/reset-password?token=...`).

Pure unit tests: no DB, no container. The recipient-locale *wiring* (which
User.locale each link is built from) is asserted in the service tests that
already capture those enqueued jobs — test_auth_service_password_reset_magic_link.py,
test_auth_service_email_change.py, and test_tenancy_service.py.
"""

import pytest

from app.core.config import get_settings
from app.core.locales import EnabledLocales, LocaleConfig
from app.core.urls import frontend_locale, frontend_url
from product import config as product_config


def _base() -> str:
    # Read per call, not once at import: conftest configures the test
    # settings (FRONTEND_URL included) after this module is collected.
    return get_settings().FRONTEND_URL


class TestFrontendLocale:
    def test_returns_an_enabled_locale_unchanged(self) -> None:
        assert frontend_locale("fi") == "fi"

    def test_falls_back_to_the_default_when_missing(self) -> None:
        assert frontend_locale(None) == "en"
        assert frontend_locale("") == "en"

    def test_falls_back_for_a_locale_the_registry_does_not_have(self) -> None:
        assert frontend_locale("xx") == "en"

    def test_falls_back_for_a_locale_the_product_has_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A stored User.locale outlives a product turning that locale off --
        # linking such a user to /fi/... would 404 them out of their own
        # password reset, since apps/web/src/routes/$locale.tsx rejects any
        # locale the registry doesn't ship.
        monkeypatch.setattr(
            product_config, "LOCALES", EnabledLocales(fi=LocaleConfig(enabled=False))
        )
        assert frontend_locale("fi") == "en"


class TestFrontendUrl:
    def test_prefixes_the_recipients_locale(self) -> None:
        assert (
            frontend_url("/reset-password?token=abc", "fi")
            == f"{_base()}/fi/reset-password?token=abc"
        )

    def test_keeps_deeper_paths_intact(self) -> None:
        assert (
            frontend_url("/accept-invitation/tok-123", "de")
            == f"{_base()}/de/accept-invitation/tok-123"
        )
        assert frontend_url("/settings/security", "fr") == f"{_base()}/fr/settings/security"

    def test_defaults_the_locale_when_none_is_known(self) -> None:
        assert frontend_url("/verify-email?token=abc") == f"{_base()}/en/verify-email?token=abc"

    def test_rejects_a_path_without_a_leading_slash(self) -> None:
        # Catches the "{FRONTEND_URL}{path}" -> "{FRONTEND_URL}/{locale}{path}"
        # migration being done by hand and half-finished, which would silently
        # produce ".../enverify-email".
        with pytest.raises(ValueError, match="must start with '/'"):
            frontend_url("verify-email", "en")
