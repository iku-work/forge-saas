from pathlib import Path

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
    assert content.count("dockerfile: Dockerfile") == 2


def _ci_workflow() -> dict:
    content = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    return yaml.safe_load(content)


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


def test_deploy_job_only_runs_on_main_and_needs_build() -> None:
    deploy = _ci_workflow()["jobs"]["deploy"]
    assert deploy["if"] == "github.ref == 'refs/heads/main'"
    assert deploy["needs"] == "build"


def test_deploy_job_runs_migrations_before_triggering_a_deploy() -> None:
    steps = _ci_workflow()["jobs"]["deploy"]["steps"]
    step_names = [s.get("name", "") for s in steps]
    migrate_index = next(i for i, n in enumerate(step_names) if "Run migrations" in n)
    deploy_index = next(i for i, n in enumerate(step_names) if "Trigger Render deploy" in n)
    assert migrate_index < deploy_index
    assert "alembic upgrade head" in steps[migrate_index]["run"]


def test_deploy_job_rolls_back_only_on_smoke_test_failure() -> None:
    steps = _ci_workflow()["jobs"]["deploy"]["steps"]
    rollback = next(s for s in steps if s.get("name") == "Roll back to previous deploy")
    assert rollback["if"] == "failure() && steps.smoke_test.outcome == 'failure'"
    assert "rollback" in rollback["run"]
