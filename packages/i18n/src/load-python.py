"""
Reference locale loader for the backend.

This mirrors src/index.ts's LOCALES map for Python consumers: it merges every
locales/<code>/*.json file into one dict keyed by namespace (the filename
without its extension), e.g. {"common": {...}, "auth": {...}, ...}.

T-068 ("Backend i18n -- locale resolution") adapts this into
backend/app/core/i18n.py, where load_translations() calls something
equivalent to load_locale() for every code in SUPPORTED_LOCALES at FastAPI
lifespan startup, and t() does the dot-notation lookup + ICU interpolation +
fallback-to-English that src/index.ts's t() already does on the TypeScript
side. Not imported by the live backend yet -- see TEMPLATE.md / agents.md
T-068 for why that wiring (including how the locales directory reaches the
backend's Docker build context) is a separate task.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"


def load_locale(code: str, locales_dir: Path | None = None) -> dict[str, Any]:
    """Merges every locales/<code>/*.json file into one dict keyed by
    namespace. Raises FileNotFoundError if the locale directory doesn't
    exist -- callers should check `code` against SUPPORTED_LOCALES first."""
    base = locales_dir if locales_dir is not None else _LOCALES_DIR
    locale_dir = base / code
    if not locale_dir.is_dir():
        raise FileNotFoundError(f"No locale data for '{code}' at {locale_dir}")

    merged: dict[str, Any] = {}
    for json_file in sorted(locale_dir.glob("*.json")):
        namespace = json_file.stem
        merged[namespace] = json.loads(json_file.read_text(encoding="utf-8"))
    return merged
