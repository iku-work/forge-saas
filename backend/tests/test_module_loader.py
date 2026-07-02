from collections.abc import Generator

import pytest

from app.core import module_loader
from app.core.modules import EnabledModules, ModuleConfig
from product import config as product_config


@pytest.fixture(autouse=True)
def _clear_modules_cache() -> Generator[None]:
    module_loader.get_modules.cache_clear()
    yield
    module_loader.get_modules.cache_clear()


def test_validate_modules_raises_on_unmet_dependency() -> None:
    modules = EnabledModules(billing=ModuleConfig(enabled=False))

    with pytest.raises(RuntimeError) as exc_info:
        module_loader.validate_modules(modules)

    assert str(exc_info.value) == (
        "Module 'notifications' requires 'billing' which is disabled.\n"
        "Fix: either set billing=ModuleConfig(enabled=True) "
        "or set notifications=ModuleConfig(enabled=False) "
        "in backend/product/config.py"
    )


def test_validate_modules_with_valid_config_does_not_raise() -> None:
    module_loader.validate_modules(EnabledModules())


def test_validate_modules_skips_non_module_config_attributes() -> None:
    # vars(modules) is iterated structurally (not via dataclasses.fields), so
    # the isinstance guard is what protects against a future EnabledModules
    # field that isn't a ModuleConfig -- exercise it directly here since no
    # such field exists on EnabledModules today.
    class _MixedAttributes:
        def __init__(self) -> None:
            self.billing = ModuleConfig(enabled=True)
            self.note = "not a module config"

    module_loader.validate_modules(_MixedAttributes())  # type: ignore[arg-type]


def test_get_modules_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[EnabledModules] = []
    original_validate = module_loader.validate_modules

    def _spy(modules: EnabledModules) -> None:
        calls.append(modules)
        original_validate(modules)

    monkeypatch.setattr(module_loader, "validate_modules", _spy)

    first = module_loader.get_modules()
    second = module_loader.get_modules()

    assert first is second
    assert len(calls) == 1


def test_is_enabled_reflects_product_config_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        product_config,
        "MODULES",
        EnabledModules(
            billing=ModuleConfig(enabled=False),
            notifications=ModuleConfig(enabled=False),
            file_storage=ModuleConfig(enabled=False),
        ),
    )

    assert module_loader.is_enabled("billing") is False


def test_is_enabled_true_for_default_config() -> None:
    assert module_loader.is_enabled("billing") is True


@pytest.fixture(autouse=True)
def _clear_branding_cache() -> Generator[None]:
    module_loader.get_branding.cache_clear()
    yield
    module_loader.get_branding.cache_clear()


def test_get_branding_reflects_product_config() -> None:
    branding = module_loader.get_branding()

    assert branding.app_name == product_config.APP_NAME
    assert branding.primary_color == product_config.PRIMARY_COLOR
    assert branding.logo_url == product_config.LOGO_URL
    assert branding.support_email == product_config.SUPPORT_EMAIL


def test_get_branding_is_cached() -> None:
    first = module_loader.get_branding()
    second = module_loader.get_branding()

    assert first is second


def test_get_branding_reflects_product_config_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(product_config, "APP_NAME", "Acme")
    monkeypatch.setattr(product_config, "SUPPORT_EMAIL", "help@acme.test")

    branding = module_loader.get_branding()

    assert branding.app_name == "Acme"
    assert branding.support_email == "help@acme.test"
