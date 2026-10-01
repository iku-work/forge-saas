"""Repo finding 16: the lockfiles must describe the dependencies actually
declared, or they pin the wrong software confidently.

`backend/pyproject.toml` declares open-ended minimums (`fastapi>=0.115`),
and the Dockerfile used to run a bare `pip install .` -- so the same commit
produced a different dependency tree on different days, and the repo's own
comments already record two incompatibilities discovered that way. The image
now installs from `requirements.lock` with `--require-hashes`.

A lock is only worth having while it is current, and the way it rots is
mundane: somebody adds a dependency to pyproject.toml and does not run
`make lock`. The build then fails in CI with a confusing pip error, or --
worse -- succeeds, because the new package happened to arrive as a
transitive dependency of something else at whatever version that pulled in.

These tests catch that here, where the message can say what to do.

They deliberately do *not* shell out to `uv --check`: uv is a lock-authoring
tool, not a runtime dependency, and a test that skips when it is missing is
a test that never runs in the one environment (CI) that matters most.
Parsing the export's own `# via` annotations gets the same answer from the
committed file alone.
"""

import re
import tomllib
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).parent.parent
PYPROJECT = BACKEND_ROOT / "pyproject.toml"
RUNTIME_LOCK = BACKEND_ROOT / "requirements.lock"
DEV_LOCK = BACKEND_ROOT / "requirements-dev.lock"

# uv writes this as the "via" for anything the project depends on directly.
PROJECT_NAME = "forge-saas-backend"

_REGENERATE = "Run `make lock` and commit both lockfiles."


def _normalise(name: str) -> str:
    """PEP 503 normalisation, near enough: the lock writes `typing-extensions`
    where pyproject may say `typing_extensions`."""
    return re.split(r"[<>=!~\[ ;]", name, maxsplit=1)[0].strip().lower().replace("_", "-")


def _declared(*, extra: str | None = None) -> set[str]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    if extra is None:
        specs = data["project"]["dependencies"]
    else:
        specs = data["project"]["optional-dependencies"][extra]
    return {_normalise(spec) for spec in specs}


def _direct_entries(lock: Path) -> set[str]:
    """The packages the lock attributes to this project rather than to
    another package's requirements."""
    text = lock.read_text(encoding="utf-8")
    found: set[str] = set()
    # Each entry runs from a package line to the next one at column zero:
    # `name==version \` then indented hashes, then an indented `# via` block.
    for match in re.finditer(r"^([A-Za-z0-9._-]+)==.*?(?=^\S|\Z)", text, re.DOTALL | re.MULTILINE):
        if PROJECT_NAME in match.group(0):
            found.add(_normalise(match.group(1)))
    return found


@pytest.mark.parametrize("lock", [RUNTIME_LOCK, DEV_LOCK], ids=["runtime", "dev"])
def test_the_lockfile_exists_and_pins_hashes(lock: Path) -> None:
    """--require-hashes is the half that makes this a supply-chain control
    rather than a version list: without digests, a re-uploaded artifact at
    the same version installs silently."""
    assert lock.exists(), f"{lock.name} is missing. {_REGENERATE}"
    text = lock.read_text(encoding="utf-8")
    assert "--hash=sha256:" in text, f"{lock.name} has no hashes. {_REGENERATE}"
    # Every pin is exact. A `>=` surviving into the lock would mean the
    # image resolves at build time again, which is the whole finding.
    loose = re.findall(r"^([A-Za-z0-9._-]+)(>=|~=|<)", text, re.MULTILINE)
    assert not loose, f"{lock.name} has unpinned entries: {loose}. {_REGENERATE}"


def test_the_runtime_lock_covers_every_declared_dependency() -> None:
    declared = _declared()
    locked = _direct_entries(RUNTIME_LOCK)

    missing = declared - locked
    assert not missing, (
        f"declared in pyproject.toml but absent from requirements.lock: "
        f"{sorted(missing)}. {_REGENERATE}"
    )

    # The other direction matters too: a dependency removed from pyproject
    # but left in the lock is still installed into the image, which is how a
    # package nobody has audited for a year stays in production.
    stale = locked - declared
    assert not stale, (
        f"in requirements.lock but no longer declared in pyproject.toml: "
        f"{sorted(stale)}. {_REGENERATE}"
    )


def test_the_dev_lock_covers_the_dev_extra_too() -> None:
    """CI installs the dev extra, so it needs its own lock -- otherwise the
    tools that decide whether a change is green (ruff, mypy, pytest) float,
    which this repo has already been bitten by twice (see pyproject.toml's
    comments on why all three are pinned exactly)."""
    declared = _declared() | _declared(extra="dev")
    locked = _direct_entries(DEV_LOCK)

    missing = declared - locked
    assert not missing, (
        f"declared in pyproject.toml but absent from requirements-dev.lock: "
        f"{sorted(missing)}. {_REGENERATE}"
    )


def test_the_pinned_tool_versions_agree_with_pyproject() -> None:
    """The three tools pyproject pins exactly must be pinned to the *same*
    version in the lock. A disagreement here means `make lock` was run
    against a pyproject that has since changed, and the tree would be
    checked by a different ruff or mypy than the one it is documented as
    being green against."""
    declared = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    exact = {
        _normalise(spec): spec.split("==")[1]
        for spec in declared["project"]["optional-dependencies"]["dev"]
        if "==" in spec
    }
    assert exact, "pyproject.toml pins no dev tool exactly -- did the pins move?"

    text = DEV_LOCK.read_text(encoding="utf-8")
    for name, version in exact.items():
        assert re.search(rf"^{re.escape(name)}=={re.escape(version)}\b", text, re.MULTILINE), (
            f"pyproject pins {name}=={version} but requirements-dev.lock disagrees. {_REGENERATE}"
        )
