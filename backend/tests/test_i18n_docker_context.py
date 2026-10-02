"""
T-068: packages/i18n/locales/ lives outside backend/'s own Docker build
context, so app.core.i18n's _BAKED_LOCALES_DIR fallback only ever gets
populated correctly if these three files stay wired together -- this is a
static check on the config text itself (matching tests/test_caddy_config.py's
pattern), not a real docker build (no Docker daemon in this test run).
"""

import os
from pathlib import Path

import pytest
import yaml

from app.core.config import get_settings
from app.core.i18n import _locales_dir
from app.workers.jobs.email import _email_renderer_cli_path

REPO_ROOT = Path(__file__).parent.parent.parent


def test_dockerfile_copies_locales_into_backend_image() -> None:
    content = (REPO_ROOT / "backend" / "Dockerfile").read_text()

    assert "COPY packages/i18n/locales ./locales" in content


def test_compose_mounts_locales_for_every_dev_service_running_the_backend() -> None:
    """Every Python service translates something -- the worker sends email,
    the scanner-worker (review R4) sends the file-infected notification -- so
    each one that runs the backend image needs the locales mounted. Keyed on
    the image rather than a count, so a new worker service is covered the day
    it is added."""
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
    backend_services = {
        name: service
        for name, service in compose["services"].items()
        if service.get("image") == "forge-saas-backend-dev"
    }

    assert {"backend", "worker", "scanner-worker"} <= backend_services.keys()
    for name, service in backend_services.items():
        assert "./packages/i18n/locales:/app/locales:ro" in service.get("volumes", []), name


def test_ci_builds_image_with_repo_root_context_and_explicit_dockerfile() -> None:
    content = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()

    assert "-f backend/Dockerfile" in content


# ── The half these checks were missing ──────────────────────────────────────
#
# Asserting the COPY exists says nothing about whether the running app can
# find what was copied, and it could not. The image holds two copies of
# `app/` -- pip-installed under site-packages, and COPYed to /app/app -- so
# `Path(__file__).parents[N]` resolves to /app under pytest and to
# site-packages under a console script (`uvicorn`, `arq`), which do not put
# the cwd on sys.path. Production got the copy with no assets beside it:
# every email failed to render, and `t()` raised KeyError on the first
# translated string. Every test passed throughout, because pytest runs from
# the repo where the _PACKAGE_ paths resolve and the baked ones are never
# reached.
#
# The Dockerfile now states both paths outright. These pin that the code
# honours them and that the Dockerfile still sets them -- one without the
# other is the same silent failure again.
def test_locales_dir_honours_the_explicit_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALES_DIR", "/somewhere/else/locales")

    assert _locales_dir() == Path("/somewhere/else/locales")


def test_renderer_path_honours_the_explicit_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_RENDERER_CLI", "/somewhere/else/cli.js")

    assert _email_renderer_cli_path().name == "cli.js"
    assert "somewhere" in str(_email_renderer_cli_path())


def test_both_are_read_per_call_not_bound_at_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """A value bound at import time is read before a test can set it -- the
    trap app.middleware.rate_limit documents. These must not have it."""
    monkeypatch.delenv("LOCALES_DIR", raising=False)
    before = _locales_dir()
    monkeypatch.setenv("LOCALES_DIR", "/changed/after/import")
    get_settings.cache_clear()

    assert _locales_dir() != before


def test_dockerfile_states_both_asset_paths() -> None:
    """The other half: the code honouring an unset variable helps nobody."""
    content = (REPO_ROOT / "backend" / "Dockerfile").read_text()

    assert "EMAIL_RENDERER_CLI=/app/email-templates/cli.js" in content
    assert "LOCALES_DIR=/app/locales" in content
    # And they must name the paths the COPYs actually write to.
    assert "COPY packages/i18n/locales ./locales" in content
    assert "/repo/packages/email-templates/dist ./email-templates" in content


def test_no_stale_env_leaks_into_the_rest_of_the_suite() -> None:
    assert os.environ.get("LOCALES_DIR") is None
    assert os.environ.get("EMAIL_RENDERER_CLI") is None
