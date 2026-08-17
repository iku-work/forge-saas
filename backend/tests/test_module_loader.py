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


@pytest.mark.parametrize(
    ("modules", "dependent", "dependency"),
    [
        pytest.param(
            EnabledModules(billing=ModuleConfig(enabled=False)),
            "notifications",
            "billing",
            id="notifications-without-billing",
        ),
        # T-290: enterprise_sso's OIDC SP (T-331) reuses authlib's client
        # registry and auth's account-linking, so SSO without oauth is a
        # half-stack, not a smaller product.
        pytest.param(
            EnabledModules(
                oauth=ModuleConfig(enabled=False),
                enterprise_sso=ModuleConfig(enabled=True),
            ),
            "enterprise_sso",
            "oauth",
            id="enterprise-sso-without-oauth",
        ),
    ],
)
def test_validate_modules_raises_on_unmet_dependency(
    modules: EnabledModules, dependent: str, dependency: str
) -> None:
    with pytest.raises(RuntimeError) as exc_info:
        module_loader.validate_modules(modules)

    assert str(exc_info.value) == (
        f"Module '{dependent}' requires '{dependency}' which is disabled.\n"
        f"Fix: either set {dependency}=ModuleConfig(enabled=True) "
        f"or set {dependent}=ModuleConfig(enabled=False) "
        f"in backend/product/config.py"
    )


def test_validate_modules_enforces_edges_a_spelled_out_config_would_drop() -> None:
    # backend/product/config.py spells every module out, and an explicitly
    # constructed ModuleConfig has an empty `requires` -- the default_factory
    # that carries the edge only runs for *omitted* fields. Reading the edges
    # off the instance would therefore have made this exact config (the one a
    # product author actually writes) boot cleanly into a broken product.
    modules = EnabledModules(
        billing=ModuleConfig(enabled=False),
        notifications=ModuleConfig(enabled=True),
        file_storage=ModuleConfig(enabled=True),
    )
    assert modules.notifications.requires == []

    with pytest.raises(RuntimeError, match="'notifications' requires 'billing'"):
        module_loader.validate_modules(modules)


def test_validate_modules_honours_an_edge_only_the_instance_declares() -> None:
    # The union half of the same rule: a product-specific module can add an
    # edge the template never declared, and it is enforced too.
    modules = EnabledModules(
        billing=ModuleConfig(enabled=False),
        notifications=ModuleConfig(enabled=False),
        file_storage=ModuleConfig(enabled=False),
        admin=ModuleConfig(enabled=True, requires=["billing"]),
    )

    with pytest.raises(RuntimeError, match="'admin' requires 'billing'"):
        module_loader.validate_modules(modules)


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
