"""
T-068: packages/i18n/locales/ lives outside backend/'s own Docker build
context, so app.core.i18n's _BAKED_LOCALES_DIR fallback only ever gets
populated correctly if these three files stay wired together -- this is a
static check on the config text itself (matching tests/test_caddy_config.py's
pattern), not a real docker build (no Docker daemon in this test run).
"""

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent


def test_dockerfile_copies_locales_into_backend_image() -> None:
    content = (REPO_ROOT / "backend" / "Dockerfile").read_text()

    assert "COPY packages/i18n/locales ./locales" in content


def test_compose_mounts_locales_for_backend_and_worker_dev_services() -> None:
    content = (REPO_ROOT / "docker-compose.yml").read_text()

    assert content.count("./packages/i18n/locales:/app/locales:ro") == 2


def test_ci_builds_image_with_repo_root_context_and_explicit_dockerfile() -> None:
    content = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()

    assert "-f backend/Dockerfile" in content
