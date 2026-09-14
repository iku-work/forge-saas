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


DEPLOY_TARGETS = ("render", "hetzner", "aws")


def _deploy_workflow(target: str) -> dict:
    path = REPO_ROOT / ".github" / "workflows" / f"deploy-{target}.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _preflight_action() -> dict:
    path = REPO_ROOT / ".github" / "actions" / "deploy-preflight" / "action.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# These four used to assert the shape of one inline `deploy` job in ci.yml.
# That job was split into a workflow per platform on 2026-09-14, and the
# invariants it guarded matter more now, not less: there are three ways to
# ship instead of one, and only one of them was ever covered. So they are
# parameterised over all three.
@pytest.mark.parametrize("target", DEPLOY_TARGETS)
def test_deploy_only_runs_on_main_and_needs_build(target: str) -> None:
    job = _ci_workflow()["jobs"][f"deploy-{target}"]
    assert "refs/heads/main" in job["if"]
    assert job["needs"] == "build"
    # Called as a reusable workflow, so the image it deploys is the one
    # `build` scanned and pushed rather than one rebuilt here.
    assert job["uses"] == f"./.github/workflows/deploy-{target}.yml"


# T-225/ASVS V12.4.2: a deploy must refuse a production env file that would
# leave uploaded files unscanned, and must do so before any migration runs
# against production. Now asserted once, in the shared preflight, which is
# exactly why the preflight exists -- three copies would have drifted.
def test_preflight_guards_against_virus_scanner_noop_before_migrating() -> None:
    steps = _preflight_action()["runs"]["steps"]
    names = [s.get("name", "") for s in steps]
    guard = next(i for i, n in enumerate(names) if "VIRUS_SCANNER" in n)
    migrate = next(i for i, n in enumerate(names) if "migrations" in n.lower())
    assert guard < migrate
    assert "exit 1" in steps[guard]["run"]


def test_preflight_migrates_with_the_branch_aware_command() -> None:
    steps = _preflight_action()["runs"]["steps"]
    migrate = next(s for s in steps if "migrations" in s.get("name", "").lower())
    # `python -m app.core.migrations`, not `alembic upgrade head`: every
    # optional module keeps its migrations on its own branch (T-062), so this
    # tree always has several heads and a bare `head` is ambiguous -- alembic
    # refuses to run at all. Asserting the ambiguous command is *absent* is
    # what stops it coming back.
    assert "python -m app.core.migrations" in migrate["run"]
    assert "alembic upgrade head" not in migrate["run"]


@pytest.mark.parametrize("target", DEPLOY_TARGETS)
def test_every_deploy_migrates_before_it_switches_traffic(target: str) -> None:
    steps = _deploy_workflow(target)["jobs"]["deploy"]["steps"]
    names = [s.get("name", "") for s in steps]
    preflight = next(i for i, n in enumerate(names) if "Preflight" in n)
    # The first step that changes anything a user can reach, per platform.
    switch_marker = {
        "render": "Trigger Render deploy",
        "hetzner": "Pull and switch",
        "aws": "Deploy the backend service",
    }[target]
    switch = next(i for i, n in enumerate(names) if switch_marker in n)
    assert preflight < switch, "a failed migration must abort before traffic moves"


@pytest.mark.parametrize("target", DEPLOY_TARGETS)
def test_every_deploy_can_roll_back_and_only_does_so_on_failure(target: str) -> None:
    steps = _deploy_workflow(target)["jobs"]["deploy"]["steps"]
    rollback = next(s for s in steps if "Roll back" in s.get("name", ""))
    # `failure()` and nothing looser: a rollback that fires on cancellation,
    # or unconditionally, is a second outage rather than a recovery.
    assert rollback["if"].startswith("failure()")
    # And something must have captured what to roll back *to*, before the
    # switch -- a rollback with no recorded previous state is decoration.
    assert any("previous" in s.get("id", "") for s in steps)


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
