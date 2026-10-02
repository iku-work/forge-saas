"""T-290: the two product config files are a mirror, and nothing checked it.

`backend/product/config.py` and the repo-root `product.config.ts` carry the
same module toggles for the two halves of the stack -- the backend 404s a
disabled module's routes, the frontend hides its UI -- and they are kept in
step by hand. A half-applied toggle gives you one without the other, which is
the failure docs/runbooks/module-toggle-guide.md warns about twice.

Nothing enforced it until now. `product.config.ts` sits at the repo root, and
every tsconfig in the workspace pins `include: ["src"]` with a matching
`rootDir`, so no package type-checks it: even its `satisfies ProductConfig`
clause is never evaluated by any tool. This file is that missing check, from
the side that can see both files at once.

T-291 extends the same comparison to `backend/tests/fixtures/module_configs/`,
where each fixture has a `product.config.ts` twin.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, get_args

import pytest
import yaml

from app.core.module_loader import validate_modules
from app.core.modules import EnabledModules, ModuleName
from product import config as product_config
from product.config import MODULES

if TYPE_CHECKING:
    from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PRODUCT_CONFIG_TS = _REPO_ROOT / "product.config.ts"
_PRODUCT_CONFIG_PY = _REPO_ROOT / "backend" / "product" / "config.py"
_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "module_configs"

# The `modules: { ... }` object literal, matched on its exact two-space
# indentation so the `locales` block that follows it cannot be swept in.
_MODULES_BLOCK = re.compile(r"^  modules: \{$(.*?)^  \},$", re.DOTALL | re.MULTILINE)
_ENTRY = re.compile(r"^\s*(\w+): (true|false),$", re.MULTILINE)


def _camel(module_name: str) -> str:
    """`file_storage` -> `fileStorage`, the convention product.config.ts uses.

    `str.capitalize()` leaves a leading digit alone, which is what makes
    `totp_2fa` come out as `totp2fa` rather than `totp2Fa`.
    """
    head, *rest = module_name.split("_")
    return head + "".join(part.capitalize() for part in rest)


def _typescript_modules(path: Path = _PRODUCT_CONFIG_TS) -> dict[str, bool]:
    source = path.read_text(encoding="utf-8")
    block = _MODULES_BLOCK.search(source)
    assert block is not None, f"no `modules: {{...}}` block found in {path}"
    return {name: value == "true" for name, value in _ENTRY.findall(block.group(1))}


def test_the_typescript_mirror_lists_every_registered_module() -> None:
    expected = {_camel(name) for name in get_args(ModuleName)}

    assert set(_typescript_modules()) == expected


def test_the_typescript_mirror_agrees_with_the_python_toggles() -> None:
    # Values, not just keys: two files that name the same modules and disagree
    # about which are on is precisely the half-applied toggle -- a backend
    # serving routes the UI will never show, or a UI offering a page the API
    # 404s.
    expected = {_camel(name): getattr(MODULES, name).enabled for name in get_args(ModuleName)}

    assert _typescript_modules() == expected


# ── T-291: the module_configs/ fixtures ─────────────────────────────────────
#
# CI's `module-matrix` job copies one of these pairs over the two real config
# files and then spends several minutes migrating, booting and building. Every
# way a fixture can be wrong is cheap to catch here and expensive to catch
# there, so all of it is checked in-process first. See that directory's
# README.md for why combination A deliberately has no fixture.

_FIXTURES = sorted(path.name for path in _FIXTURE_DIR.glob("*.py"))


def _load_fixture(path: Path) -> ModuleType:
    """Execute a fixture the way `product.config` itself is executed."""
    spec = importlib.util.spec_from_file_location(f"module_config_fixture_{path.stem}", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Deliberately not registered in sys.modules: nothing imports these by
    # name, and leaving one behind would make a later test's `product.config`
    # lookup ambiguous.
    spec.loader.exec_module(module)
    return module


def _settings(module: ModuleType) -> dict[str, Any]:
    """The product-owned constants a config file exports, minus MODULES.

    Keyed on the UPPER_CASE naming both files use for settings, which is also
    what separates them from the imported `EnabledModules`/`LocaleConfig`
    symbols sharing the namespace.
    """
    return {
        name: value
        for name, value in vars(module).items()
        if name.isupper() and not name.startswith("_") and name != "MODULES"
    }


def test_there_is_a_fixture_for_every_combination_the_matrix_job_runs() -> None:
    # Not "at least" -- an orphaned fixture nobody runs is as much of a
    # problem as a missing one, since the two below are the only things
    # keeping the guards in this file honest about what CI actually does.
    workflow = yaml.safe_load((_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    legs = next(
        job["strategy"]["matrix"]["include"]
        for job in workflow["jobs"].values()
        if "fixture" in str(job.get("strategy", {}).get("matrix", {}))
    )
    assert sorted(f"{leg['fixture']}.py" for leg in legs if leg["fixture"]) == _FIXTURES


@pytest.mark.parametrize("filename", _FIXTURES)
def test_every_fixture_is_a_loadable_config_that_passes_validate_modules(filename: str) -> None:
    module = _load_fixture(_FIXTURE_DIR / filename)

    modules = module.MODULES
    assert isinstance(modules, EnabledModules)
    # The same call app.main makes at boot. A fixture that violates a
    # dependency edge would otherwise fail the matrix job minutes in, on a
    # container that refuses to start, rather than here.
    validate_modules(modules)


@pytest.mark.parametrize("filename", _FIXTURES)
def test_every_fixture_agrees_with_its_typescript_twin(filename: str) -> None:
    module = _load_fixture(_FIXTURE_DIR / filename)
    twin = _FIXTURE_DIR / f"{Path(filename).stem}.config.ts"
    assert twin.exists(), (
        f"{filename} has no {twin.name}: the matrix job copies both files or the "
        f"combination is only half applied"
    )

    expected = {
        _camel(name): getattr(module.MODULES, name).enabled for name in get_args(ModuleName)
    }

    assert _typescript_modules(twin) == expected


@pytest.mark.parametrize("filename", _FIXTURES)
def test_every_fixture_differs_from_the_shipped_config_only_in_its_modules(
    filename: str,
) -> None:
    """Branding, support and locales are copied verbatim into each fixture.

    That duplication is only safe while something notices it going stale: a
    setting added to backend/product/config.py and not to the fixtures would
    otherwise surface as an app that refuses to boot halfway through the
    matrix job (or worse, one that boots against a *different* product
    identity than the combination claims to test).
    """
    module = _load_fixture(_FIXTURE_DIR / filename)

    assert _settings(module) == _settings(product_config), (
        f"{filename} has drifted from backend/product/config.py outside its MODULES block"
    )


@pytest.mark.parametrize("filename", _FIXTURES)
def test_every_typescript_twin_differs_from_the_shipped_one_only_in_its_modules(
    filename: str,
) -> None:
    twin = _FIXTURE_DIR / f"{Path(filename).stem}.config.ts"
    placeholder = "  modules: {},"

    def _without_modules(path: Path) -> str:
        return _MODULES_BLOCK.sub(placeholder, path.read_text(encoding="utf-8"))

    assert _without_modules(twin) == _without_modules(_PRODUCT_CONFIG_TS), (
        f"{twin.name} has drifted from product.config.ts outside its `modules` block"
    )


def test_the_shipped_config_is_where_combination_a_gets_its_modules() -> None:
    """Combination A copies nothing, so this is the file CI runs it against.

    Stated as a test because the matrix job's A leg has no fixture to point
    at, and "no fixture" is otherwise indistinguishable from "someone deleted
    it". See fixtures/module_configs/README.md.
    """
    assert _PRODUCT_CONFIG_PY.exists()
    assert not (_FIXTURE_DIR / "default.py").exists(), (
        "combination A must run the product's own committed config, not a "
        "template-owned copy of it -- see fixtures/module_configs/README.md"
    )
