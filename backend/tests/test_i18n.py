"""
T-068: app.core.i18n is the backend's translation engine -- loads
packages/i18n/locales/<code>/*.json once into a process-wide cache and
exposes t() (dot-notation lookup + fallback-to-English + {placeholder}
interpolation), get_supported_locales(), and get_default_locale(), both of
which read from backend/product/config.py (see app.core.module_loader for
the identical lazy-import-for-monkeypatch pattern this mirrors).
"""

from collections.abc import Generator
from pathlib import Path

import pytest

from app.core import i18n
from product import config as product_config


@pytest.fixture(autouse=True)
def _clear_translations_cache() -> Generator[None]:
    i18n._translations.cache_clear()
    yield
    i18n._translations.cache_clear()


def test_get_supported_locales_reflects_product_config() -> None:
    assert i18n.get_supported_locales() == list(product_config.SUPPORTED_LOCALES)


def test_get_supported_locales_reflects_product_config_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(product_config, "SUPPORTED_LOCALES", ["en"])

    assert i18n.get_supported_locales() == ["en"]


def test_get_default_locale_reflects_product_config() -> None:
    assert i18n.get_default_locale() == product_config.DEFAULT_LOCALE


def test_get_default_locale_reflects_product_config_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(product_config, "DEFAULT_LOCALE", "fi")

    assert i18n.get_default_locale() == "fi"


def test_load_translations_populates_cache_for_every_supported_locale() -> None:
    i18n.load_translations()

    catalogs = i18n._translations()
    assert set(catalogs) == set(i18n.get_supported_locales())
    assert "errors" in catalogs["en"]
    assert "emails" in catalogs["en"]


def test_t_resolves_a_plain_key_in_the_default_locale() -> None:
    assert i18n.t("errors.fallback") == "An error occurred."


def test_t_resolves_a_key_in_an_explicit_locale() -> None:
    assert i18n.t("errors.fallback", "fi") == "Tapahtui virhe."


def test_t_falls_back_to_english_when_key_missing_in_target_locale() -> None:
    # billing.upgrade_button is deliberately untranslated in fi (T-067's
    # partial worked-example locale) -- the English value must come back,
    # never the raw key string.
    assert i18n.t("billing.upgrade_button", "fi") == "Upgrade"


def test_t_falls_back_to_default_locale_for_unrecognized_locale_code() -> None:
    assert i18n.t("errors.fallback", "ja") == i18n.t("errors.fallback", "en")


def test_t_raises_key_error_when_missing_from_both_locales() -> None:
    with pytest.raises(KeyError, match="missing key"):
        i18n.t("errors.does_not_exist")


def test_t_interpolates_a_plain_placeholder() -> None:
    assert i18n.t("errors.plan_not_found", plan_slug="pro") == "Plan 'pro' could not be found."


def test_t_interpolation_leaves_unmatched_placeholders_untouched() -> None:
    assert i18n.t("errors.plan_not_found") == "Plan '{plan_slug}' could not be found."


def test_t_returns_message_unchanged_when_no_params_and_no_placeholders() -> None:
    assert i18n.t("errors.not_found") == "The requested resource could not be found."


def test_load_locale_merges_every_namespace_file() -> None:
    merged = i18n._load_locale("en")

    assert merged["errors"]["fallback"] == "An error occurred."
    assert merged["emails"]["subject"]["welcome"] == "Welcome to ForgeSaaS"


def test_locales_dir_prefers_package_dir_when_present() -> None:
    assert i18n._locales_dir() == i18n._PACKAGE_LOCALES_DIR


def test_locales_dir_falls_back_to_baked_dir_when_package_dir_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing = tmp_path / "does-not-exist"
    baked = tmp_path / "baked"
    monkeypatch.setattr(i18n, "_PACKAGE_LOCALES_DIR", missing)
    monkeypatch.setattr(i18n, "_BAKED_LOCALES_DIR", baked)

    assert i18n._locales_dir() == baked
