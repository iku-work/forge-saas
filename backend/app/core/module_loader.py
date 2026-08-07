from dataclasses import dataclass
from functools import lru_cache

from app.core.modules import EnabledModules, ModuleConfig, ModuleName
from app.core.support import SupportConfig, validate_support


@dataclass(frozen=True)
class Branding:
    app_name: str
    primary_color: str
    logo_url: str
    support_email: str


def validate_modules(modules: EnabledModules) -> None:
    """
    Called at startup. Raises RuntimeError with a clear message if any
    enabled module declares a `requires` dependency that is disabled.
    Fast-fail: developers learn about misconfiguration at boot, not at runtime.
    """
    for name, config in vars(modules).items():
        if not isinstance(config, ModuleConfig):
            continue
        if config.enabled:
            for dep in config.requires:
                dep_cfg = getattr(modules, dep)
                if not dep_cfg.enabled:
                    raise RuntimeError(
                        f"Module '{name}' requires '{dep}' which is disabled.\n"
                        f"Fix: either set {dep}=ModuleConfig(enabled=True) "
                        f"or set {name}=ModuleConfig(enabled=False) "
                        f"in backend/product/config.py"
                    )


@lru_cache(maxsize=1)
def get_modules() -> EnabledModules:
    # Imported lazily (not at module scope) so that monkeypatching
    # product.config.MODULES in tests is picked up here, instead of being
    # invisible behind a name already bound at this module's import time.
    from product.config import MODULES  # noqa: PLC0415

    validate_modules(MODULES)
    return MODULES


def is_enabled(module: ModuleName) -> bool:
    config: ModuleConfig = getattr(get_modules(), module)
    return config.enabled


@lru_cache(maxsize=1)
def get_branding() -> Branding:
    # Lazy import for the same reason as get_modules() above: a name bound at
    # this module's import time wouldn't see a test's monkeypatch of
    # product.config's attributes.
    from product.config import APP_NAME, LOGO_URL, PRIMARY_COLOR, SUPPORT_EMAIL  # noqa: PLC0415

    return Branding(
        app_name=APP_NAME,
        primary_color=PRIMARY_COLOR,
        logo_url=LOGO_URL,
        support_email=SUPPORT_EMAIL,
    )


@lru_cache(maxsize=1)
def get_support_config() -> SupportConfig:
    """T-262: which support tool the product wired up, plus the URL every
    frontend falls back to when there's no widget to open (mobile always,
    web when SUPPORT_TOOL is "none"). Lazy import + lru_cache for the same
    reasons as get_branding() above."""
    from product.config import (  # noqa: PLC0415
        SUPPORT_EMAIL,
        SUPPORT_TOOL,
        SUPPORT_URL,
    )

    validate_support(SUPPORT_TOOL, SUPPORT_URL)
    # Concatenation, not an f-string: f"mailto:{...}" has the exact shape
    # tests/test_cache_keys.py's sweep flags as a hand-built Redis key.
    return SupportConfig(tool=SUPPORT_TOOL, url=SUPPORT_URL or "mailto:" + SUPPORT_EMAIL)
