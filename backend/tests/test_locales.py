import pytest

from app.core.locales import EnabledLocales, LocaleConfig, validate_locales


def test_validate_locales_raises_when_default_is_disabled() -> None:
    locales = EnabledLocales(en=LocaleConfig(enabled=False))

    with pytest.raises(RuntimeError) as exc_info:
        validate_locales(locales, "en")

    assert str(exc_info.value) == (
        "DEFAULT_LOCALE 'en' is not an enabled locale.\n"
        "Fix: enable it (e.g. en=LocaleConfig(enabled=True)) or "
        "change DEFAULT_LOCALE in backend/product/config.py"
    )


def test_validate_locales_raises_when_default_is_unknown() -> None:
    with pytest.raises(RuntimeError, match="DEFAULT_LOCALE 'xx' is not an enabled locale"):
        validate_locales(EnabledLocales(), "xx")


def test_validate_locales_with_valid_config_does_not_raise() -> None:
    validate_locales(EnabledLocales(), "en")


def test_validate_locales_allows_a_disabled_non_default_locale() -> None:
    validate_locales(EnabledLocales(ar=LocaleConfig(enabled=False)), "en")
