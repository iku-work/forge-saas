"""
T-262: which support tool the product uses, and where "Contact support"
should send a user who isn't served by a JavaScript widget.

Deliberately tool-agnostic: the template never imports an Intercom/Crisp/
Plain SDK, it only reports *which* tool is configured and hands the frontends
a URL to fall back to. Wiring the vendor widget itself is product-side code
(see docs/runbooks/support-tool-integration.md).

Mirrors locales.py's shape -- a Literal of the allowed values plus a
validate_* that fails fast at boot, since backend/product/config.py is plain
Python with no schema behind it and a typo like SUPPORT_TOOL = "intercomm"
would otherwise surface as a silently dead support button.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

SupportTool = Literal["none", "intercom", "crisp", "plain", "helpscout", "custom"]

SUPPORT_TOOLS: frozenset[str] = frozenset(get_args(SupportTool))

# A support link is a link the *user's own browser* opens, so unlike
# app.core.url_guard's outbound-SSRF gate this only rules out schemes that
# would be dangerous or useless to hand a frontend (javascript:, data:, a
# bare "support.example.com" with no scheme at all).
_ALLOWED_URL_SCHEMES = ("https://", "http://", "mailto:")


@dataclass(frozen=True)
class SupportConfig:
    tool: SupportTool
    # Always populated: falls back to `mailto:<SUPPORT_EMAIL>` when the
    # product set no SUPPORT_URL, so neither frontend has to reimplement
    # that resolution (and mobile, which has no widget to fall back *from*,
    # always has something to open).
    url: str


def validate_support(tool: str, url: str | None) -> None:
    """Called at startup via get_support_config() below -- same fail-fast
    timing as validate_modules()/validate_locales()."""
    if tool not in SUPPORT_TOOLS:
        allowed = ", ".join(sorted(SUPPORT_TOOLS))
        raise RuntimeError(
            f"SUPPORT_TOOL '{tool}' is not a supported value.\n"
            f"Fix: set SUPPORT_TOOL to one of {allowed} in backend/product/config.py"
        )
    if url is not None and not url.startswith(_ALLOWED_URL_SCHEMES):
        raise RuntimeError(
            f"SUPPORT_URL '{url}' must start with https://, http://, or mailto:.\n"
            f"Fix: correct SUPPORT_URL in backend/product/config.py "
            f"(or set it to None to fall back to mailto:SUPPORT_EMAIL)"
        )
