"""
T-262: backend/product/config.py is plain Python with no schema behind it,
so SUPPORT_TOOL/SUPPORT_URL get the same fail-fast-at-boot validation
modules.py and locales.py already give MODULES/LOCALES -- a typo must be a
startup error, not a support button that silently goes nowhere.
"""

from collections.abc import Generator

import pytest

from app.core import module_loader
from app.core.support import SUPPORT_TOOLS, validate_support
from product import config as product_config


@pytest.fixture(autouse=True)
def _clear_cache() -> Generator[None]:
    module_loader.get_support_config.cache_clear()
    yield
    module_loader.get_support_config.cache_clear()


def test_every_documented_tool_is_accepted() -> None:
    for tool in SUPPORT_TOOLS:
        validate_support(tool, None)


def test_unknown_tool_is_rejected_with_a_fix_hint() -> None:
    with pytest.raises(RuntimeError, match="intercomm"):
        validate_support("intercomm", None)


def test_url_without_a_usable_scheme_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="must start with"):
        validate_support("none", "support.example.com")


@pytest.mark.parametrize(
    "url",
    ["https://help.example.com", "http://localhost:3000/support", "mailto:help@example.com"],
)
def test_accepts_the_schemes_a_frontend_can_open(url: str) -> None:
    validate_support("none", url)


def test_defaults_to_no_tool_and_the_support_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(product_config, "SUPPORT_EMAIL", "help@example.com")

    config = module_loader.get_support_config()

    assert config.tool == "none"
    assert config.url == "mailto:help@example.com"


def test_a_bad_product_config_fails_when_it_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(product_config, "SUPPORT_TOOL", "zendesk")

    with pytest.raises(RuntimeError, match="zendesk"):
        module_loader.get_support_config()
