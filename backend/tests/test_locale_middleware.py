"""
T-068: app.middleware.locale resolves a request's locale before any
dependency or handler runs (see that module's docstring for the full
3-step priority, only steps 2-3 of which live here -- step 1, the
authenticated user's own User.locale, is covered by
tests/test_dependencies.py::test_get_current_user_sets_request_state_locale_from_user).
"""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.requests import Request as StarletteRequest

from app.core.locales import EnabledLocales, LocaleConfig
from app.middleware.locale import LocaleMiddleware, _negotiate, get_locale
from product import config as product_config

# ---- _negotiate -------------------------------------------------------------


def test_negotiate_returns_default_when_header_absent() -> None:
    assert _negotiate(None) == "en"


def test_negotiate_returns_default_when_header_empty() -> None:
    assert _negotiate("") == "en"


def test_negotiate_matches_a_single_supported_locale() -> None:
    assert _negotiate("fi") == "fi"


def test_negotiate_picks_highest_priority_supported_locale() -> None:
    assert _negotiate("fi,en;q=0.5") == "fi"


def test_negotiate_falls_back_to_default_for_unsupported_locale() -> None:
    assert _negotiate("ja") == "en"


def test_negotiate_truncates_region_subtag_to_match_bare_code() -> None:
    # "en-US" must truncate down to "en" to match SUPPORTED_LOCALES -- see
    # the module's sep="-" comment for why babel's default sep="_" wouldn't.
    assert _negotiate("en-US,fi;q=0.5") == "en"


def test_negotiate_reflects_product_config_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        product_config,
        "LOCALES",
        EnabledLocales(
            en=LocaleConfig(enabled=True),
            fi=LocaleConfig(enabled=False),
            de=LocaleConfig(enabled=False),
            es=LocaleConfig(enabled=False),
            fr=LocaleConfig(enabled=False),
            ar=LocaleConfig(enabled=False),
        ),
    )
    monkeypatch.setattr(product_config, "DEFAULT_LOCALE", "en")

    assert _negotiate("fi") == "en"


# ---- get_locale ---------------------------------------------------------------


def test_get_locale_returns_state_locale_when_set() -> None:
    request = StarletteRequest(scope={"type": "http", "state": {"locale": "fi"}})

    assert get_locale(request) == "fi"


def test_get_locale_returns_default_when_state_locale_unset() -> None:
    request = StarletteRequest(scope={"type": "http"})

    assert get_locale(request) == "en"


# ---- LocaleMiddleware (end-to-end via a throwaway app) -----------------------


def _build_probe_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(LocaleMiddleware)

    @app.get("/probe")
    async def probe(request: Request) -> dict[str, str]:
        return {"locale": request.state.locale}

    return app


def test_middleware_sets_request_state_locale_from_accept_language() -> None:
    client = TestClient(_build_probe_app())

    response = client.get("/probe", headers={"Accept-Language": "fi,en;q=0.5"})

    assert response.json() == {"locale": "fi"}


def test_middleware_defaults_when_accept_language_absent() -> None:
    client = TestClient(_build_probe_app())

    response = client.get("/probe")

    assert response.json() == {"locale": "en"}


def test_middleware_falls_back_for_unsupported_accept_language() -> None:
    client = TestClient(_build_probe_app())

    response = client.get("/probe", headers={"Accept-Language": "ja"})

    assert response.json() == {"locale": "en"}
