"""
Verifies the import-linter setup itself (T-012), not any feature module:
  - the real backend/.importlinter config, run exactly as `cd backend &&
    lint-imports` would, currently passes (modules are still empty stubs).
  - the "Four-layer rule" contract actually catches a §0.1 violation
    (a service importing a router) once a module has real code in it.
  - a module that only has *some* of its four layers built still passes —
    layers are declared optional so the contract doesn't hard-fail for
    modules under incremental construction (T-013 adds models.py only;
    repository/service/router land in later tasks).

Each scenario gets its own throwaway root package under tmp_path with its
own .importlinter config, so the synthetic violation never touches the
real app/ tree. sys.path / sys.modules are restored after every test since
`lint_imports()` mutates sys.path as a side effect.
"""

import logging
import sys
from collections.abc import Generator
from pathlib import Path

import pytest
from importlinter.cli import lint_imports

BACKEND_DIR = Path(__file__).parent.parent
EXIT_STATUS_SUCCESS = 0
EXIT_STATUS_ERROR = 1


@pytest.fixture(autouse=True)
def _restore_logger_disabled_flags() -> Generator[None]:
    # lint_imports() configures its own logging via logging.config.dictConfig()
    # (importlinter/cli.py::_configure_logging), which takes the stdlib default
    # disable_existing_loggers=True and so sets `disabled = True` on every
    # logger already created in this process -- every app.* logger included --
    # for the rest of the session. Left unrestored, no application log record
    # is emitted in any test that runs after this file, pytest's `caplog`
    # included, and the failure is silent. Snapshot/restore, like conftest's
    # _reset_breakers and _reset_fernet_cache do for their own global state.
    manager = logging.getLogger().manager
    was_disabled = {
        name: existing.disabled
        for name, existing in manager.loggerDict.items()
        if isinstance(existing, logging.Logger)
    }
    yield
    for name, disabled in was_disabled.items():
        existing = manager.loggerDict.get(name)
        if isinstance(existing, logging.Logger):
            existing.disabled = disabled


@pytest.fixture
def isolated_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    yield tmp_path
    sys.path[:] = [p for p in sys.path if p != str(tmp_path)]
    for name in list(sys.modules):
        if name == "fakeapp" or name.startswith("fakeapp."):
            del sys.modules[name]


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _write_layers_config(root: Path) -> None:
    _write(
        root / ".importlinter",
        """
[importlinter]
root_package = fakeapp

[importlinter:contract:layers]
name = Four-layer rule
type = layers
layers =
    (router)
    (service)
    (repository)
    (models)
containers =
    fakeapp.modules.*
""",
    )


def test_real_config_passes_against_current_codebase(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(BACKEND_DIR)

    exit_code = lint_imports(config_filename=".importlinter", no_cache=True)

    assert exit_code == EXIT_STATUS_SUCCESS


def test_layers_contract_catches_a_service_importing_a_router(isolated_package: Path) -> None:
    root = isolated_package
    _write_layers_config(root)
    _write(root / "fakeapp" / "__init__.py")
    _write(root / "fakeapp" / "modules" / "__init__.py")
    _write(root / "fakeapp" / "modules" / "widget" / "__init__.py")
    _write(root / "fakeapp" / "modules" / "widget" / "models.py", "class Widget: ...\n")
    _write(
        root / "fakeapp" / "modules" / "widget" / "repository.py",
        "from fakeapp.modules.widget.models import Widget\n",
    )
    _write(
        root / "fakeapp" / "modules" / "widget" / "service.py",
        "from fakeapp.modules.widget.repository import Widget\n"
        "from fakeapp.modules.widget.router import router  # violates the layer rule\n",
    )
    _write(
        root / "fakeapp" / "modules" / "widget" / "router.py",
        "router = object()\n",
    )

    exit_code = lint_imports(config_filename=".importlinter", no_cache=True)

    assert exit_code == EXIT_STATUS_ERROR


def test_layers_contract_passes_for_a_compliant_module(isolated_package: Path) -> None:
    root = isolated_package
    _write_layers_config(root)
    _write(root / "fakeapp" / "__init__.py")
    _write(root / "fakeapp" / "modules" / "__init__.py")
    _write(root / "fakeapp" / "modules" / "widget" / "__init__.py")
    _write(root / "fakeapp" / "modules" / "widget" / "models.py", "class Widget: ...\n")
    _write(
        root / "fakeapp" / "modules" / "widget" / "repository.py",
        "from fakeapp.modules.widget.models import Widget\n",
    )
    _write(
        root / "fakeapp" / "modules" / "widget" / "service.py",
        "from fakeapp.modules.widget.repository import Widget\n",
    )
    _write(
        root / "fakeapp" / "modules" / "widget" / "router.py",
        "from fakeapp.modules.widget.service import Widget\n",
    )

    exit_code = lint_imports(config_filename=".importlinter", no_cache=True)

    assert exit_code == EXIT_STATUS_SUCCESS


def test_layers_contract_does_not_require_every_layer_to_exist(isolated_package: Path) -> None:
    root = isolated_package
    _write_layers_config(root)
    _write(root / "fakeapp" / "__init__.py")
    _write(root / "fakeapp" / "modules" / "__init__.py")
    _write(root / "fakeapp" / "modules" / "widget" / "__init__.py")
    _write(root / "fakeapp" / "modules" / "widget" / "models.py", "class Widget: ...\n")

    exit_code = lint_imports(config_filename=".importlinter", no_cache=True)

    assert exit_code == EXIT_STATUS_SUCCESS
