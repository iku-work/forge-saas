"""scripts/deploy-box.sh, the only thing CI's SSH key can run on a box.

Run for real against a git checkout with an origin, with `docker` replaced by
a stand-in that records what it was asked to do. What matters most is what it
refuses: a commit that is not on main, a command that is not one of its own,
and settings that compose would act on.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "deploy-box.sh"
LOCK = REPO_ROOT / "scripts" / "lock-deploy-key.sh"

ENV = "ENVIRONMENT={environment}\nDOMAIN=app.example.com\nSECRET_KEY=x\n"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603 -- fixed argv: test fixture
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],  # noqa: S607 -- git on PATH
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class Box:
    """A box's home directory: ~/forge, a checkout of a GitHub-shaped origin."""

    def __init__(self, tmp_path: Path) -> None:
        bash = shutil.which("bash")
        if bash is None or shutil.which("git") is None:
            pytest.skip("needs bash and git")
        self.bash = bash
        self.home = tmp_path / "home"
        self.app = self.home / "forge"
        self.log = tmp_path / "docker.log"
        work, origin = tmp_path / "work", tmp_path / "origin.git"
        _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
        _git(tmp_path, "init", "-q", "-b", "main", str(work))
        # The ancestry the script checks: one commit from before it existed,
        # then main, then a branch nobody merged.
        (work / "README").write_text("x", encoding="utf-8")
        _git(work, "add", ".")
        _git(work, "commit", "-q", "-m", "old")
        self.old = _git(work, "rev-parse", "HEAD")
        (work / "scripts").mkdir()
        (work / "scripts" / "deploy-box.sh").write_text("# stand-in\n", encoding="utf-8")
        (work / "scripts" / "wait-for-release.sh").write_text(
            'echo "waited for $BACKEND_IMAGE $CADDY_IMAGE on $PROD_DOMAIN"\n', encoding="utf-8"
        )
        _git(work, "add", ".")
        _git(work, "commit", "-q", "-m", "main")
        self.main = _git(work, "rev-parse", "HEAD")
        _git(work, "checkout", "-q", "-b", "unmerged")
        (work / "evil").write_text("x", encoding="utf-8")
        _git(work, "add", ".")
        _git(work, "commit", "-q", "-m", "unmerged")
        self.unmerged = _git(work, "rev-parse", "HEAD")
        _git(work, "push", "-q", str(origin), "main", "unmerged")
        self.home.mkdir()
        _git(self.home, "clone", "-q", str(origin), str(self.app))
        # Shaped like the bootstrap's clone, and fetched from the local origin.
        _git(self.app, "remote", "set-url", "origin", "git@github.com:Owner/Repo.git")
        _git(
            self.app,
            "config",
            f"url.{origin.as_posix()}.insteadOf",
            "git@github.com:Owner/Repo.git",
        )
        (self.app / "backend").mkdir()
        (self.app / ".env.postgres").write_text("POSTGRES_PASSWORD=pw\n", encoding="utf-8")
        (self.home / "forge-backup.env").write_text("BACKUP_REMOTE=r2:bucket\n", encoding="utf-8")
        stubs = tmp_path / "bin"
        stubs.mkdir()
        docker = stubs / "docker"
        docker.write_text(
            "#!/usr/bin/env bash\n"
            'if [ "$1" = login ]; then echo "$* stdin=$(cat)" >> "$DOCKER_LOG"; exit 0; fi\n'
            'echo "$* | $BACKEND_IMAGE | $CADDY_IMAGE" >> "$DOCKER_LOG"\n',
            newline="\n",
        )
        docker.chmod(0o755)
        self.path = f"{stubs}{os.pathsep}{os.environ['PATH']}"

    def run(self, command: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
        """As the locked key runs it: the client's command in SSH_ORIGINAL_COMMAND."""
        return subprocess.run(  # noqa: S603 -- fixed argv: the script under test
            [self.bash, str(SCRIPT)],
            input=stdin,
            env={
                **os.environ,
                "HOME": str(self.home),
                "PATH": self.path,
                "DOCKER_LOG": str(self.log),
                "SSH_ORIGINAL_COMMAND": command,
            },
            capture_output=True,
            text=True,
            check=False,
        )

    def docker_calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []


@pytest.fixture
def box(tmp_path: Path) -> Box:
    return Box(tmp_path)


@pytest.mark.parametrize(
    "command",
    [
        "",
        "bash",
        "current; touch pwned",
        "prune now",
        "switch",
        "migrate abc123 someone",
        "rollback HEAD",
        "verify origin/main",
    ],
)
def test_anything_but_its_own_commands_is_refused(box: Box, command: str) -> None:
    result = box.run(command)
    assert result.returncode != 0
    assert box.docker_calls() == []
    assert not (box.app / "pwned").exists()


@pytest.mark.parametrize("which", ["old", "unmerged"])
def test_only_a_commit_on_main_with_this_script_can_be_deployed(box: Box, which: str) -> None:
    (box.app / "backend" / ".env.next").write_text(ENV.format(environment="prod"), encoding="utf-8")
    result = box.run(f"migrate {getattr(box, which)} someone", stdin="token\n")
    assert result.returncode != 0
    assert {"old": "older than scripts/deploy-box.sh", "unmerged": "not a commit on main"}[
        which
    ] in result.stderr
    assert box.docker_calls() == []
    assert _git(box.app, "rev-parse", "HEAD") == box.main, "the checkout must not move"


@pytest.mark.parametrize(
    ("env", "says"),
    [
        (ENV.format(environment="prod") + "COMPOSE_PROFILES=x\n", "COMPOSE_ or DOCKER_"),
        (ENV.format(environment="prod") + "export DOCKER_HOST=tcp://evil\n", "COMPOSE_ or DOCKER_"),
        ("ENVIRONMENT=prod\nSECRET_KEY=x\n", "no DOMAIN"),
        (ENV.format(environment="dev"), "must be prod or staging"),
    ],
)
def test_env_refuses_what_compose_would_act_on(box: Box, env: str, says: str) -> None:
    result = box.run("env", stdin=env)
    assert result.returncode != 0
    assert says in result.stderr
    assert not (box.app / "backend" / ".env.next").exists()


@pytest.mark.parametrize(
    ("environment", "edge"),
    [
        ("prod", "ghcr.io/owner/repo-caddy:{sha}"),
        ("staging", "ghcr.io/owner/repo-caddy:{sha}-staging"),
    ],
)
def test_a_deploy_runs_this_repositorys_images(box: Box, environment: str, edge: str) -> None:
    env = ENV.format(environment=environment)
    (box.app / "backend" / ".env").write_text(
        "ENVIRONMENT=prod\nDOMAIN=old.example.com\n", encoding="utf-8"
    )
    assert box.run("env", stdin=env).returncode == 0

    migrate = box.run(f"migrate {box.main} someone", stdin="the-token\n")
    assert migrate.returncode == 0, migrate.stderr
    images = f"ghcr.io/owner/repo:{box.main} | {edge.format(sha=box.main)}"
    calls = box.docker_calls()
    # The token reached `docker login` on stdin and nowhere else.
    assert calls[0] == "login ghcr.io -u someone --password-stdin stdin=the-token"
    assert all("the-token" not in call for call in calls[1:])
    assert any(
        call.startswith("compose ") and " pull " in call and call.endswith(images) for call in calls
    )
    assert (
        calls[-1]
        .split(" | ")[0]
        .endswith(
            "run --rm --no-deps -e MIGRATIONS_DATABASE_URL backend python -m app.core.migrations"
        )
    )
    assert (box.app / "backend" / ".env").read_text(encoding="utf-8") == env
    assert (
        (box.app / "backend" / ".env.prev")
        .read_text(encoding="utf-8")
        .startswith("ENVIRONMENT=prod")
    )

    assert box.run(f"switch {box.main}").returncode == 0
    ups = [call for call in box.docker_calls() if "up -d --no-deps" in call]
    assert [up.split(" --no-deps ")[1] for up in ups] == [
        f"caddy | {images}",
        f"backend worker scanner-worker | {images}",
    ]

    verify = box.run(f"verify {box.main}")
    assert verify.returncode == 0, verify.stderr
    assert f"waited for {images.replace(' | ', ' ')} on app.example.com" in verify.stdout


def test_switch_refuses_a_release_that_was_not_migrated(box: Box) -> None:
    (box.app / "backend" / ".env").write_text(ENV.format(environment="prod"), encoding="utf-8")
    _git(box.app, "checkout", "-q", "--detach", box.old)
    result = box.run(f"switch {box.main}")
    assert result.returncode != 0
    assert "Run migrate first" in result.stderr
    assert box.docker_calls() == []


def test_migrate_refuses_a_box_without_off_box_backups(box: Box) -> None:
    (box.home / "forge-backup.env").write_text("BACKUP_DIR=/var/backups/forge\n", encoding="utf-8")
    (box.app / "backend" / ".env").write_text(ENV.format(environment="prod"), encoding="utf-8")
    result = box.run(f"migrate {box.main} someone", stdin="token\n")
    assert result.returncode != 0
    assert "Refusing to migrate" in result.stderr
    assert not any("app.core.migrations" in call for call in box.docker_calls())


def test_the_lock_forces_the_deploy_script_and_nothing_else() -> None:
    script = LOCK.read_text(encoding="utf-8")
    assert 'line="restrict,command=\\"/usr/bin/bash $SCRIPT\\" $type $blob ci-deploy"' in script
    assert "SCRIPT=$APP_DIR/scripts/deploy-box.sh" in script
    # An unrestricted copy of the same key would still open a shell.
    assert 'grep -vF " $blob"' in script


@pytest.mark.parametrize(
    ("target", "environment", "passes"),
    [
        ("staging", "staging", True),
        ("production", "prod", True),
        ("production", "staging", False),
        ("staging", "prod", False),
    ],
)
def test_a_deploy_refuses_another_environments_settings(
    tmp_path: Path, target: str, environment: str, *, passes: bool
) -> None:
    # Staging's machine-account token pasted into production would otherwise
    # ship staging's settings to the production box.
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("needs bash")
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "deploy-hetzner.yml").read_text(encoding="utf-8")
    )
    step = next(
        s
        for s in workflow["jobs"]["deploy"]["steps"]
        if s.get("name") == "Check the env is this environment's"
    )
    (tmp_path / "prod.env").write_text(ENV.format(environment=environment), encoding="utf-8")
    github_env = tmp_path / "github_env"
    result = subprocess.run(  # noqa: S603 -- fixed argv: the step under test
        [bash, "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        env={**os.environ, "TARGET": target, "GITHUB_ENV": str(github_env)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is passes, result.stderr
    if passes:
        assert github_env.read_text(encoding="utf-8") == "DOMAIN=app.example.com\n"
