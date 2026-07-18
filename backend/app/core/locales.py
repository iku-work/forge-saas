"""
T-231: per-locale toggle for the demo product, mirroring modules.py's
EnabledModules/ModuleConfig shape (`billing=ModuleConfig(enabled=True)`) so
enabling/disabling a locale reads the same way enabling/disabling a module
does, instead of editing a bare list of string codes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

LocaleCode = Literal["en", "fi", "de", "es", "fr", "ar"]


@dataclass(frozen=True)
class LocaleConfig:
    enabled: bool = True


@dataclass(frozen=True)
class EnabledLocales:
    en: LocaleConfig = field(default_factory=lambda: LocaleConfig(enabled=True))
    fi: LocaleConfig = field(default_factory=lambda: LocaleConfig(enabled=True))
    de: LocaleConfig = field(default_factory=lambda: LocaleConfig(enabled=True))
    es: LocaleConfig = field(default_factory=lambda: LocaleConfig(enabled=True))
    fr: LocaleConfig = field(default_factory=lambda: LocaleConfig(enabled=True))
    ar: LocaleConfig = field(default_factory=lambda: LocaleConfig(enabled=True))


def validate_locales(locales: EnabledLocales, default: str) -> None:
    """
    Called at startup (via app/core/i18n.py's get_supported_locales(), same
    fail-fast-at-boot timing as modules.py's validate_modules()). Raises
    RuntimeError if DEFAULT_LOCALE names a locale that's disabled or doesn't
    exist -- developers learn about misconfiguration at boot, not the first
    time a request needs the fallback locale.
    """
    enabled = {code for code, config in vars(locales).items() if config.enabled}
    if default not in enabled:
        raise RuntimeError(
            f"DEFAULT_LOCALE '{default}' is not an enabled locale.\n"
            f"Fix: enable it (e.g. {default}=LocaleConfig(enabled=True)) or "
            f"change DEFAULT_LOCALE in backend/product/config.py"
        )
