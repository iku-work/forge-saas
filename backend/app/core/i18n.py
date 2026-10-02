"""
T-068: backend-side translation engine. Mirrors packages/i18n/src/index.ts's
LOCALES map + t() for the TypeScript side -- same dot-notation lookup and
fallback-to-English semantics. See packages/i18n/src/load-python.py for the
reference loader this adapts, and packages/i18n/README.md for the key
conventions every catalog entry follows.

ICU plural/select syntax (e.g. notifications.json's
"{count, plural, one {...} other {...}}", used on the TypeScript side) is
*not* interpreted here -- no backend-consumed key needs it yet (errors.json
and emails.json are plain `{placeholder}` substitution only). _PLACEHOLDER_RE
only matches a single bare `{word}` token, so a plural-syntax string passes
through unsubstituted instead of raising; add real ICU support here if a
future module (e.g. notifications, T-101+) needs to render one from the
backend.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.locales import validate_locales

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

# packages/i18n/locales/ lives outside backend/'s own Docker build context --
# backend/Dockerfile's COPY can't reach a sibling top-level directory, and
# the dev compose volume only binds ./backend. _BAKED_LOCALES_DIR is what
# docker-compose.yml's backend/worker volumes and backend/Dockerfile's COPY
# step populate for exactly that case (see both files); on a plain host
# checkout (pytest, local `uvicorn` outside Docker, CI's OpenAPI-schema step)
# _PACKAGE_LOCALES_DIR resolves directly since the whole repo is present.
_PACKAGE_LOCALES_DIR = Path(__file__).resolve().parents[3] / "packages" / "i18n" / "locales"
_BAKED_LOCALES_DIR = Path(__file__).resolve().parents[2] / "locales"


# Same trap as app.workers.jobs.email's renderer path, and it bites here more
# quietly. The image holds two copies of `app/` -- pip-installed under
# site-packages, and COPYed to /app/app -- and a console script (`uvicorn`,
# `arq`) does not put the cwd on sys.path, so production imports the
# site-packages one while `parents[2]` was written for the other. Missing
# locales raise nothing at startup: `load_translations()` just caches empty
# catalogs, and the failure surfaces later as a KeyError from `t()` on
# whichever request first needs a translated string.
def _locales_dir() -> Path:
    override = get_settings().LOCALES_DIR
    if override:
        return Path(override)
    if _PACKAGE_LOCALES_DIR.is_dir():
        return _PACKAGE_LOCALES_DIR
    return _BAKED_LOCALES_DIR


def get_supported_locales() -> list[str]:
    """Returns the enabled locale codes from product.config.LOCALES (T-231's
    per-locale toggle, mirroring get_modules()'s EnabledModules) -- frontend
    reads this via /config endpoint."""
    # Lazy import so a test's monkeypatch.setattr(product_config, ...) is
    # picked up here instead of being invisible behind a name already bound
    # at this module's import time -- same reasoning as module_loader.py.
    from product.config import DEFAULT_LOCALE, LOCALES  # noqa: PLC0415

    validate_locales(LOCALES, DEFAULT_LOCALE)
    return [code for code, config in vars(LOCALES).items() if config.enabled]


def get_default_locale() -> str:
    from product.config import DEFAULT_LOCALE  # noqa: PLC0415

    return DEFAULT_LOCALE


def _load_locale(code: str) -> dict[str, Any]:
    """Merges every locales/<code>/*.json file into one dict keyed by namespace."""
    locale_dir = _locales_dir() / code
    merged: dict[str, Any] = {}
    for json_file in sorted(locale_dir.glob("*.json")):
        merged[json_file.stem] = json.loads(json_file.read_text(encoding="utf-8"))
    return merged


@lru_cache(maxsize=1)
def _translations() -> dict[str, dict[str, Any]]:
    return {code: _load_locale(code) for code in get_supported_locales()}


def load_translations() -> None:
    """Called from app lifespan startup.
    Reads packages/i18n/locales/<code>/*.json for every code in
    SUPPORTED_LOCALES. Merges all files in a locale into one nested dict."""
    _translations.cache_clear()
    _translations()


def _resolve(messages: dict[str, Any], key: str) -> str | None:
    namespace, _, rest = key.partition(".")
    node: Any = messages.get(namespace)
    for segment in rest.split(".") if rest else ():
        if not isinstance(node, dict):
            return None
        node = node.get(segment)
    return node if isinstance(node, str) else None


def t(key: str, locale: str | None = None, **params: Any) -> str:  # noqa: ANN401 -- interpolation values are caller-defined
    """Lookup with dot-notation, ICU interpolation, fallback to en.
    Raises KeyError only if the key is missing in both target and en
    (this is a developer error -- keys must exist in en first)."""
    default_locale = get_default_locale()
    effective_locale = locale or default_locale
    catalogs = _translations()
    message = _resolve(catalogs.get(effective_locale, {}), key)
    if message is None and effective_locale != default_locale:
        message = _resolve(catalogs.get(default_locale, {}), key)
    if message is None:
        raise KeyError(
            f"i18n: missing key {key!r} in both {effective_locale!r} and {default_locale!r}"
        )
    if not params:
        return message
    return _PLACEHOLDER_RE.sub(lambda m: str(params.get(m.group(1), m.group(0))), message)
