import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent


def test_dockerfile_is_multi_stage_with_pinned_base_image() -> None:
    content = (REPO_ROOT / "backend" / "Dockerfile").read_text()
    images = re.findall(r"^FROM\s+(\S+)", content, re.MULTILINE)
    assert len(images) >= 2, "build tools must stay out of the runtime stage"
    for image in images:
        assert ":" in image, image
        assert not image.endswith(":latest"), image


def test_dockerfile_runs_as_non_root_user() -> None:
    content = (REPO_ROOT / "backend" / "Dockerfile").read_text()
    assert "useradd --system" in content
    assert "USER app" in content


def test_docker_compose_prod_uses_hardened_dockerfile() -> None:
    services = yaml.safe_load((REPO_ROOT / "docker-compose.prod.yml").read_text())["services"]
    builds = [s["build"] for s in services.values() if "build" in s]
    assert builds
    # T-244: `backend/Dockerfile` with a repo-root build context, not
    # `Dockerfile` with `context: ./backend` -- that Dockerfile COPYs
    # packages/i18n/locales, which ./backend can't reach, so the previous
    # spelling could never build. Same invocation the CI build job uses.
    for build in builds:
        assert build.get("dockerfile") != "Dockerfile.dev", build
        if build.get("dockerfile") == "backend/Dockerfile":
            assert build["context"] in (".", "./"), build


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
def test_deploy_only_runs_on_main_and_needs_the_full_gate(target: str) -> None:
    job = _ci_workflow()["jobs"][f"deploy-{target}"]
    assert "refs/heads/main" in job["if"]
    # The weekly scheduled run is on main too; only a push may deploy.
    assert "github.event_name == 'push'" in job["if"]
    # C2: the browser suite and the ZAP baseline gate the deploy as well as
    # the image build (test and security arrive transitively through build).
    assert set(job["needs"]) == {"build", "e2e-web", "zap-baseline"}
    # Called as a reusable workflow, so the image it deploys is the one
    # `build` scanned and pushed rather than one rebuilt here.
    assert job["uses"] == f"./.github/workflows/deploy-{target}.yml"


@pytest.mark.parametrize("target", DEPLOY_TARGETS)
def test_caller_grants_every_permission_the_deploy_job_asks_for(target: str) -> None:
    # C3: a reusable workflow can only narrow its caller's grant. A permission
    # the called job declares but the caller does not grant is silently
    # absent -- which is how deploy-aws's OIDC `id-token: write` never worked.
    caller = _ci_workflow()["jobs"][f"deploy-{target}"].get("permissions", {})
    wanted = _deploy_workflow(target)["jobs"]["deploy"].get("permissions", {})
    for scope, level in wanted.items():
        assert caller.get(scope) == level, f"caller must grant {scope}: {level}"


# T-225/ASVS V12.4.2: a deploy must refuse a production env file that would
# leave uploaded files unscanned, before any platform is touched. Asserted
# once, in the shared preflight, which is exactly why the preflight exists --
# three copies would have drifted.
def test_preflight_guards_against_virus_scanner_noop() -> None:
    steps = _preflight_action()["runs"]["steps"]
    guard = next(s for s in steps if "VIRUS_SCANNER" in s.get("name", ""))
    assert "exit 1" in guard["run"]


# C4: the runner never reaches the production database. The preflight used
# to run the new image against it from GitHub's network.
def test_preflight_does_not_migrate() -> None:
    for step in _preflight_action()["runs"]["steps"]:
        assert "app.core.migrations" not in step.get("run", ""), step.get("name")
        assert "migrat" not in step.get("name", "").lower(), step.get("name")


# The step that migrates *on the target*, and the first step that changes
# anything a user can reach, per platform.
MIGRATE_STEP = {
    "render": "Confirm Render migrates in pre-deploy",
    "hetzner": "Migrate on the box",
    "aws": "Run migrations as a one-off task",
}
SWITCH_STEP = {
    "render": "Trigger Render deploy",
    "hetzner": "Pull and switch",
    "aws": "Deploy the backend service",
}


def _step_text(step: dict) -> str:
    """A step's script plus its env -- Render's migration command is in the
    job env, checked against the service by the script."""
    env = " ".join(str(v) for v in step.get("env", {}).values())
    return f"{step.get('run', '')} {env}"


@pytest.mark.parametrize("target", DEPLOY_TARGETS)
def test_every_deploy_migrates_on_the_target_before_it_switches(target: str) -> None:
    job = _deploy_workflow(target)["jobs"]["deploy"]
    steps = job["steps"]
    names = [s.get("name", "") for s in steps]
    migrate = next(i for i, n in enumerate(names) if MIGRATE_STEP[target] in n)
    switch = next(i for i, n in enumerate(names) if SWITCH_STEP[target] in n)
    assert migrate < switch, "a failed migration must abort before traffic moves"

    text = _step_text(steps[migrate]) + " " + " ".join(str(v) for v in job["env"].values())
    # AWS spells the command as a JSON array for the container override.
    text = " ".join(re.sub(r"[\[\]\",]", " ", text).split())
    # `python -m app.core.migrations`, not `alembic upgrade head`: every
    # optional module keeps its migrations on its own branch (T-062), so this
    # tree always has several heads and a bare `head` is ambiguous -- alembic
    # refuses to run at all. Asserting the ambiguous command is *absent* is
    # what stops it coming back.
    assert "python -m app.core.migrations" in text
    assert "alembic upgrade head" not in text
    # On the target: over SSH, as an ECS task, or as Render's pre-deploy --
    # never `docker run` on the runner.
    assert "docker run" not in steps[migrate].get("run", "")


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


# C5: `outcome == 'success'` skipped the rollback for a switch that failed
# halfway -- some containers on the new image, some on the old -- which is
# the state that most needs undoing.
@pytest.mark.parametrize("target", ["hetzner", "aws"])
def test_rollback_also_covers_a_half_finished_switch(target: str) -> None:
    steps = _deploy_workflow(target)["jobs"]["deploy"]["steps"]
    rollback = next(s for s in steps if "Roll back" in s.get("name", ""))
    assert "!= 'skipped'" in rollback["if"]
    assert "== 'success'" not in rollback["if"]


def _all_deploy_run_steps() -> list[tuple[str, str]]:
    found = [
        (f"deploy-{t}: {s.get('name', '')}", s["run"])
        for t in DEPLOY_TARGETS
        for s in _deploy_workflow(t)["jobs"]["deploy"]["steps"]
        if "run" in s
    ]
    found += [
        (f"preflight: {s.get('name', '')}", s["run"])
        for s in _preflight_action()["runs"]["steps"]
        if "run" in s
    ]
    return found


# C4: `${{ }}` is substituted into a script's *source* before bash parses it,
# so a `'` or `$(` in a secret -- or in a manual run's free-text image-tag --
# became shell running with the deploy's credentials. Every value must reach
# a script through `env:`, where it is data.
def test_no_deploy_script_templates_a_value_into_its_source() -> None:
    offenders = [name for name, run in _all_deploy_run_steps() if "${{" in run]
    assert offenders == []


def test_preflight_writes_a_hostile_env_file_verbatim(tmp_path: Path) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("needs bash")
    step = next(s for s in _preflight_action()["runs"]["steps"] if s.get("id") == "write-env")
    hostile = "DB_PASSWORD=it's $(id) `id` \"x\" ${HOME}\nSECRET='$(touch pwned)'\n"
    output = tmp_path / "github_output"
    result = subprocess.run(  # noqa: S603 -- fixed argv: the step under test
        [bash, "-c", step["run"]],
        cwd=tmp_path,
        env={**os.environ, "PROD_ENV_FILE": hostile, "GITHUB_OUTPUT": str(output)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "prod.env").read_bytes() == hostile.encode()
    assert not (tmp_path / "pwned").exists()


def _write_env_from_bitwarden(
    tmp_path: Path, secrets: list[dict[str, str]]
) -> subprocess.CompletedProcess[str]:
    """Run Hetzner's real "Write prod.env from Bitwarden" step with `bws`
    replaced by a script that prints `secrets`."""
    bash, jq = shutil.which("bash"), shutil.which("jq")
    if bash is None or jq is None:
        pytest.skip("needs bash and jq")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "secrets.json").write_text(json.dumps(secrets), encoding="utf-8")
    fake = bin_dir / "bws"
    fake.write_text(
        f"#!/bin/sh\ncat '{(tmp_path / 'secrets.json').as_posix()}'\n", encoding="utf-8"
    )
    fake.chmod(0o755)
    steps = _deploy_workflow("hetzner")["jobs"]["deploy"]["steps"]
    step = next(s for s in steps if s.get("name") == "Write prod.env from Bitwarden")
    # The flags GitHub runs a `run:` step with.
    return subprocess.run(  # noqa: S603 -- fixed argv: the step under test
        [bash, "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=False,
    )


def test_bitwarden_env_quotes_pems_and_leaves_out_other_consumers(tmp_path: Path) -> None:
    # A public key: gitleaks flags any private-key header, even a fake one.
    pem = "-----BEGIN PUBLIC KEY-----\nabc\n-----END PUBLIC KEY-----"
    result = _write_env_from_bitwarden(
        tmp_path,
        [
            {"key": "JWT_PRIVATE_KEY", "value": pem},
            {"key": "JWT_PUBLIC_KEY", "value": f'"{pem}"\n'},
            # Pasted with a trailing newline, the way six values were on
            # 2026-10-26: it must not become a quoted two-line value.
            {"key": "DATABASE_URL", "value": "postgresql+asyncpg://u:p$(id)@db/forge\n"},
            {"key": "REDIS_URL", "value": "'rediss://redis:6379/0'"},
            {"key": "GRAFANA_CLOUD_TOKEN", "value": "glc_x"},
            {"key": "BACKUP_SFTP_PASSWORD", "value": "x"},
            {"key": "VITE_SENTRY_DSN", "value": "x"},
        ],
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "prod.env").read_text(encoding="utf-8") == (
        f'JWT_PRIVATE_KEY="{pem}"\n'
        f'JWT_PUBLIC_KEY="{pem}"\n'
        "DATABASE_URL=postgresql+asyncpg://u:p$(id)@db/forge\n"
        "REDIS_URL=rediss://redis:6379/0\n"
    )
    assert "::add-mask::-----BEGIN PUBLIC KEY-----" in result.stdout


@pytest.mark.parametrize(
    ("secrets", "message"),
    [
        (
            [{"key": "ENCRYPTION_KEY", "value": "a"}, {"key": "ENCRYPTION_KEY", "value": "b"}],
            "Defined more than once in Bitwarden: ENCRYPTION_KEY",
        ),
        ([{"key": "BACKUP_AGE_IDENTITY", "value": "AGE-SECRET-KEY-1X"}], "BACKUP_AGE_IDENTITY"),
        ([], "can read no secrets"),
        # The 2026-10-26 JWT_PRIVATE_KEY: an opening quote, and the END line
        # and closing quote lost in the paste.
        (
            [{"key": "JWT_PRIVATE_KEY", "value": '"-----BEGIN PUBLIC KEY-----\nabc'}],
            "Malformed in Bitwarden (an unclosed quote, or a PEM with no END line): "
            "JWT_PRIVATE_KEY",
        ),
        (
            [{"key": "JWT_PUBLIC_KEY", "value": "-----BEGIN PUBLIC KEY-----\nabc"}],
            "JWT_PUBLIC_KEY",
        ),
    ],
)
def test_bitwarden_env_refuses_what_would_ship_broken(
    tmp_path: Path, secrets: list[dict[str, str]], message: str
) -> None:
    result = _write_env_from_bitwarden(tmp_path, secrets)
    assert result.returncode != 0
    assert message in result.stderr
    assert not (tmp_path / "prod.env").exists()


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
    assert any("pip-audit" in s["run"] for s in audit_steps)
    assert any("pnpm audit" in s["run"] for s in audit_steps)
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


# C1 (production-readiness plan 2026-09-29), OWASP A08: every Python install
# in CI goes through a hash-pinned lock, and the project itself is added with
# --no-deps so it cannot pull a fresh resolution in behind the lock. A bare
# `pip install -e .` resolved afresh per run, so CI audited a different
# dependency set from the one the image ships.
@pytest.mark.owasp("A08")
def test_every_python_install_in_ci_uses_the_hashed_lock() -> None:
    runs = [
        line.strip()
        for job in _ci_workflow()["jobs"].values()
        for step in job.get("steps", [])
        for line in step.get("run", "").splitlines()
        if line.strip().startswith("pip install")
    ]
    assert runs, "no pip installs found -- has the workflow moved?"
    for line in runs:
        if "-r " in line:
            assert "--require-hashes" in line, line
            assert line.split("-r ", 1)[1].split()[0].endswith(".lock"), line
        else:
            assert "--no-deps" in line, f"{line!r} would resolve outside the lock"


def test_pip_audit_reads_the_lock_not_the_environment() -> None:
    steps = _ci_workflow()["jobs"]["security"]["steps"]
    audit = next(s for s in steps if "pip-audit" in s.get("run", ""))
    assert "-r requirements-dev.lock" in audit["run"]


def test_semgrep_is_not_a_project_dependency() -> None:
    # Its pins (pyjwt<2.14) leaked into the runtime lock through the
    # universal resolution; see the note in pyproject.toml.
    for lock in ("requirements.lock", "requirements-dev.lock"):
        text = (REPO_ROOT / "backend" / lock).read_text(encoding="utf-8")
        assert "\nsemgrep==" not in text, lock


def test_every_backend_image_service_gets_the_internal_ca_under_tls() -> None:
    # The DSNs name /certs/ca.crt, so a service running the backend image
    # without the mount cannot reach Redis or Postgres at all. scanner-worker
    # shipped without it and crash-looped on its first production boot.
    prod = yaml.safe_load((REPO_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8"))
    tls = yaml.safe_load((REPO_ROOT / "docker-compose.tls.yml").read_text(encoding="utf-8"))
    backend_image = prod["services"]["backend"]["image"]
    users = {
        name for name, service in prod["services"].items() if service.get("image") == backend_image
    }
    assert users >= {"backend", "worker", "scanner-worker"}
    for name in users:
        assert "./certs:/certs:ro" in tls["services"].get(name, {}).get("volumes", []), name


@pytest.mark.parametrize("target", DEPLOY_TARGETS)
def test_a_deploy_needs_an_explicit_target(target: str) -> None:
    # Review 2026-10-01: unset used to mean render, so a repository with no
    # variables was one push away from a deploy nobody chose.
    condition = _ci_workflow()["jobs"][f"deploy-{target}"]["if"]
    assert f"vars.DEPLOY_TARGET == '{target}'" in condition
    assert "== ''" not in condition


def test_hetzner_starts_caddy_on_its_own_before_the_backend() -> None:
    # Caddy carries the storage alias the backend's boot check needs. Started
    # in the same `up` as the backend, compose waits for a backend that cannot
    # become healthy without it: the first deploy to a fresh box (2026-10-26).
    steps = _deploy_workflow("hetzner")["jobs"]["deploy"]["steps"]
    for name in ("Pull and switch", "Roll back to the previous image"):
        run = next(s["run"] for s in steps if s.get("name") == name)
        ups = [line.strip() for line in run.splitlines() if " up -d " in line]
        assert ups == [
            "docker compose $COMPOSE up -d --no-deps caddy",
            "docker compose $COMPOSE up -d --no-deps backend worker scanner-worker",
        ], name


def test_hetzner_verifies_every_service_before_declaring_success() -> None:
    # Review OCT-05: /health/ready alone passed while a worker crash-looped.
    steps = _deploy_workflow("hetzner")["jobs"]["deploy"]["steps"]
    names = [s.get("name", "") for s in steps]
    verify = next(i for i, n in enumerate(names) if n.startswith("Verify every service"))
    assert names.index("Pull and switch") < verify < names.index("Smoke test")
    for service in ("backend", "worker", "scanner-worker", "caddy"):
        assert f"check {service} " in steps[verify]["run"], service


def test_module_matrix_runs_weekly_not_on_every_merge() -> None:
    # ~45 billed minutes a run (2026-10-01): too dear for every push to main.
    workflow = _ci_workflow()
    triggers = workflow[True]  # PyYAML reads the bare `on:` key as True
    assert "schedule" in triggers
    assert "workflow_dispatch" in triggers
    condition = workflow["jobs"]["module-matrix"]["if"]
    assert "'schedule'" in condition
    assert "refs/heads/main" not in condition


def test_e2e_runs_in_the_playwright_image_matching_the_installed_version() -> None:
    # Baselines are generated in that image; the bare runner renders other
    # fonts. A tag that drifts from @playwright/test also finds no browsers.
    run = next(
        s["run"]
        for s in _ci_workflow()["jobs"]["e2e-web"]["steps"]
        if s.get("name") == "Run Playwright (chromium + webkit)"
    )
    tag = re.search(r"mcr\.microsoft\.com/playwright:v([\d.]+)-", run)
    assert tag, run
    # The lockfile, not package.json: a `^` range let a routine lockfile
    # update install a newer playwright while the range still "matched".
    lock = yaml.safe_load((REPO_ROOT / "pnpm-lock.yaml").read_text(encoding="utf-8"))
    installed = {
        key.split("@")[-1]
        for key in lock["packages"]
        if key.startswith(("playwright@", "playwright-core@", "@playwright/test@"))
    }
    assert installed == {tag[1]}, f"image v{tag[1]}, lockfile {sorted(installed)}"
