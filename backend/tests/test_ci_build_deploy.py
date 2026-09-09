from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent


def test_dockerfile_is_multi_stage_with_pinned_base_image() -> None:
    content = (REPO_ROOT / "backend" / "Dockerfile").read_text()
    assert content.count("FROM python:3.12-slim") == 2
    assert "AS builder" in content
    assert "AS runtime" in content


def test_dockerfile_runs_as_non_root_user() -> None:
    content = (REPO_ROOT / "backend" / "Dockerfile").read_text()
    assert "useradd --system" in content
    assert "USER app" in content


def test_docker_compose_prod_uses_hardened_dockerfile() -> None:
    content = (REPO_ROOT / "docker-compose.prod.yml").read_text()
    assert "dockerfile: Dockerfile.dev" not in content
    # T-244: `backend/Dockerfile` with a repo-root build context, not
    # `Dockerfile` with `context: ./backend` -- that Dockerfile COPYs
    # packages/i18n/locales, which ./backend can't reach, so the previous
    # spelling could never build. Same invocation the CI build job uses.
    assert content.count("dockerfile: backend/Dockerfile") == 2


def _ci_workflow() -> dict:
    content = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    return yaml.safe_load(content)


def _zap_full_workflow() -> dict:
    content = (REPO_ROOT / ".github" / "workflows" / "zap-full.yml").read_text()
    return yaml.safe_load(content)


# T-225/ASVS V9.1.2, V9.1.3: an independent TLS cipher/version scan of
# STAGING_URL runs alongside the ZAP full scan, same dormant-until-configured
# job.
def test_zap_full_workflow_runs_testssl_against_staging() -> None:
    steps = _zap_full_workflow()["jobs"]["zap-full-scan"]["steps"]
    testssl_step = next(s for s in steps if "testssl" in s.get("name", "").lower())
    assert "drwetter/testssl.sh" in testssl_step["run"]
    assert "STAGING_URL" in testssl_step["run"]


def test_test_job_copies_env_file_before_running_pytest() -> None:
    # app/core/db.py builds the engine at import time, which runs the moment
    # conftest.py is collected -- backend/.env must exist on disk before that
    # first pytest invocation, monkeypatch'd fixtures run too late for it.
    steps = _ci_workflow()["jobs"]["test"]["steps"]
    step_names = [s.get("name", "") for s in steps]
    env_index = next(i for i, n in enumerate(step_names) if n == "Copy test environment file")
    pytest_index = next(i for i, n in enumerate(step_names) if "Backend tests" in n)
    assert env_index < pytest_index


def test_build_job_needs_test_and_security() -> None:
    workflow = _ci_workflow()
    assert set(workflow["jobs"]["build"]["needs"]) == {"test", "security"}


def test_build_job_scans_image_before_pushing() -> None:
    steps = _ci_workflow()["jobs"]["build"]["steps"]
    step_names = [s.get("name", "") for s in steps]
    scan_index = next(i for i, n in enumerate(step_names) if "Trivy image scan" in n)
    push_index = next(i for i, n in enumerate(step_names) if n == "Push image")
    assert scan_index < push_index


# T-225/ASVS V14.2.5: an SBOM of the exact shipped image, generated before
# the image is pushed (same ordering guarantee as the trivy scan above).
def test_build_job_generates_sbom_before_pushing() -> None:
    steps = _ci_workflow()["jobs"]["build"]["steps"]
    step_names = [s.get("name", "") for s in steps]
    sbom_index = next(i for i, n in enumerate(step_names) if "Generate SBOM" in n)
    push_index = next(i for i, n in enumerate(step_names) if n == "Push image")
    assert sbom_index < push_index


def test_deploy_job_only_runs_on_main_and_needs_build() -> None:
    deploy = _ci_workflow()["jobs"]["deploy"]
    assert deploy["if"] == "github.ref == 'refs/heads/main'"
    assert deploy["needs"] == "build"


# T-225/ASVS V12.4.2: the deploy job must refuse to ship a production env
# file that would leave uploaded files unscanned, and must do so before any
# migration/deploy step runs against production.
def test_deploy_job_guards_against_virus_scanner_noop_before_migrating() -> None:
    steps = _ci_workflow()["jobs"]["deploy"]["steps"]
    step_names = [s.get("name", "") for s in steps]
    guard_index = next(i for i, n in enumerate(step_names) if "Guard against VIRUS_SCANNER" in n)
    migrate_index = next(i for i, n in enumerate(step_names) if "Run migrations" in n)
    assert guard_index < migrate_index
    assert "VIRUS_SCANNER=noop" in steps[guard_index]["run"]
    assert "exit 1" in steps[guard_index]["run"]


def test_deploy_job_runs_migrations_before_triggering_a_deploy() -> None:
    steps = _ci_workflow()["jobs"]["deploy"]["steps"]
    step_names = [s.get("name", "") for s in steps]
    migrate_index = next(i for i, n in enumerate(step_names) if "Run migrations" in n)
    deploy_index = next(i for i, n in enumerate(step_names) if "Trigger Render deploy" in n)
    assert migrate_index < deploy_index
    # `python -m app.core.migrations`, not `alembic upgrade head`: every
    # optional module keeps its migrations on its own branch (T-062), so this
    # tree always has several heads and a bare `head` is ambiguous -- alembic
    # refuses to run at all. That is why the production migration step could
    # never have succeeded until it was changed, and asserting the ambiguous
    # command is *absent* is what stops it coming back.
    run = steps[migrate_index]["run"]
    assert "python -m app.core.migrations" in run
    assert "alembic upgrade head" not in run


def test_deploy_job_rolls_back_only_on_smoke_test_failure() -> None:
    steps = _ci_workflow()["jobs"]["deploy"]["steps"]
    rollback = next(s for s in steps if s.get("name") == "Roll back to previous deploy")
    assert rollback["if"] == "failure() && steps.smoke_test.outcome == 'failure'"
    assert "rollback" in rollback["run"]


# OWASP A06 (vulnerable components): T-212's mapping asserts the dependency
# audit tools are wired into a job that gates the pipeline (`build` needs
# `security`, per test_build_job_needs_test_and_security above) and none of
# the audit steps opt out of failing the job via `continue-on-error`.
@pytest.mark.owasp("A06")
def test_security_job_runs_dependency_audits_non_optionally() -> None:
    steps = _ci_workflow()["jobs"]["security"]["steps"]
    audit_steps = [
        s for s in steps if "pip-audit" in s.get("run", "") or "pnpm audit" in s.get("run", "")
    ]
    assert len(audit_steps) == 2
    assert all("continue-on-error" not in s for s in audit_steps)


# OWASP A03 (injection): community Semgrep rules (brought in by `--config
# auto`) include SQL-injection / raw-query detections; this just confirms the
# ruleset that carries them is actually in the invocation, not opted out of.
@pytest.mark.owasp("A03")
def test_security_job_semgrep_step_uses_registry_auto_config() -> None:
    steps = _ci_workflow()["jobs"]["security"]["steps"]
    semgrep_step = next(s for s in steps if "semgrep" in s.get("run", "").lower())
    assert "--config auto" in semgrep_step["run"]
    assert "continue-on-error" not in semgrep_step


# OWASP A08 (integrity failures): `--frozen-lockfile` makes `pnpm install`
# fail rather than silently rewrite pnpm-lock.yaml -- the CI-side half of
# "dependencies installed match what was committed" that T-030's webhook
# signature check covers on the application side.
@pytest.mark.owasp("A08")
def test_all_frontend_installs_use_frozen_lockfile() -> None:
    workflow = _ci_workflow()
    install_steps = [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if step.get("name") == "Install frontend dependencies"
    ]
    assert len(install_steps) > 0
    assert all("--frozen-lockfile" in step["run"] for step in install_steps)
