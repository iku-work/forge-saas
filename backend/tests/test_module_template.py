"""
T-250: `_template` is a complete, compilable reference module -- and it is
invisible to the running application.

Two halves, and the second is the one that has teeth. The template is real
Python that ruff and mypy --strict check like any other module, which is what
makes `make new-module` (T-251) produce a green starting point instead of a
pile of near-code. But because it is real Python living under
app/modules/, it also carries a table class and a router that must never
reach the application: nothing may register `Item` as a table, serve
`/api/v1/__MODULE_NAME__/`, or list `_template` among the product's modules.

The exclusions are all *explicit*, not conventional -- app/api/v1/__init__.py
lists its routers by hand, app/models/__init__.py lists its models by hand,
and app/core/modules.py's ModuleName is a closed Literal. The one place that
relied on nothing at all was Alembic: `target_metadata` is the process-wide
SQLModel.metadata, so any import of app.modules._template.models anywhere in
the process (tests/test_read_replica.py sweeps every app.modules.*.repository
through pkgutil, for one) would have put the scaffold's table in front of
autogenerate. That guard now lives in alembic/env.py and is proved by
test_conditional_migrations.py's
test_autogenerate_never_proposes_the_template_scaffolds_table.
"""

import ast
import json
import subprocess
import sys
from pathlib import Path
from typing import get_args

from fastapi.routing import iter_route_contexts

from app.core.module_loader import get_modules
from app.core.modules import ModuleName
from app.main import app

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEMPLATE_DIR = _BACKEND_DIR / "app" / "modules" / "_template"
_TEST_TEMPLATE = _BACKEND_DIR / "tests" / "modules" / "_template_test.py.tmpl"

# The generator (T-251) substitutes these two, and nothing else.
_PLACEHOLDERS = {"__MODULE_NAME__": "zztest", "__ModelName__": "Zztest"}

_EXPECTED_FILES = [
    "__init__.py",
    "models.py",
    "schemas.py",
    "repository.py",
    "service.py",
    "router.py",
    "README.md",
]


def _render(source: str) -> str:
    for placeholder, value in _PLACEHOLDERS.items():
        source = source.replace(placeholder, value)
    return source


def _run(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — fixed argv, no shell
        [sys.executable, "-m", *args],
        cwd=_BACKEND_DIR,
        capture_output=True,
        text=True,
        # Explicit, not the platform default: the template's comments are
        # UTF-8 (§, --), and on Windows `text=True` alone would encode the
        # piped source as cp1252, which ruff rejects as invalid UTF-8.
        encoding="utf-8",
        input=stdin,
        check=False,
    )


# --- the template is real, working code --------------------------------------


def test_the_module_has_all_five_files_plus_a_readme() -> None:
    missing = [name for name in _EXPECTED_FILES if not (_TEMPLATE_DIR / name).is_file()]
    assert missing == [], f"_template is missing {missing}"


def test_the_test_template_ships_alongside_it() -> None:
    # Deliberately not a .py file: pytest must not collect the template
    # itself, and ruff/mypy must not be asked to resolve
    # `app.modules.__MODULE_NAME__`, which only exists after generation.
    assert _TEST_TEMPLATE.is_file()
    assert not (_TEST_TEMPLATE.parent / "_template_test.py").exists()


def test_ruff_is_clean_on_the_template_as_is() -> None:
    result = _run("ruff", "check", str(_TEMPLATE_DIR))
    assert result.returncode == 0, result.stdout + result.stderr


def test_ruff_format_is_clean_on_the_template_as_is() -> None:
    result = _run("ruff", "format", "--check", str(_TEMPLATE_DIR))
    assert result.returncode == 0, result.stdout + result.stderr


def test_mypy_strict_is_clean_on_the_template_as_is() -> None:
    # The reason the template imports its own modules relatively
    # (`from .models import Item`): an absolute
    # `app.modules.__MODULE_NAME__.models` is unresolvable until the module
    # is generated, so mypy could never check the template itself.
    result = _run("mypy", "--strict", str(_TEMPLATE_DIR))
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_test_template_is_ruff_clean_once_rendered() -> None:
    # Checked through --stdin-filename rather than a temp file so ruff.toml's
    # `tests/**` per-file-ignores (asserts, untyped fixtures) apply exactly as
    # they will to the generated tests/modules/test_<name>.py.
    rendered = _render(_TEST_TEMPLATE.read_text(encoding="utf-8"))
    result = _run(
        "ruff",
        "check",
        "--stdin-filename",
        "tests/modules/test_zztest.py",
        "-",
        stdin=rendered,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_every_template_file_still_parses_once_the_placeholders_are_substituted() -> None:
    # Guards the property T-251's substitution depends on: the placeholders
    # sit only where a plain name may go, so rendering can never produce a
    # syntax error.
    sources = [*_TEMPLATE_DIR.glob("*.py"), _TEST_TEMPLATE]
    assert len(sources) > 1
    for path in sources:
        rendered = _render(path.read_text(encoding="utf-8"))
        ast.parse(rendered, filename=str(path))  # raises SyntaxError on failure


def test_the_readme_lists_the_manual_follow_ups() -> None:
    readme = (_TEMPLATE_DIR / "README.md").read_text(encoding="utf-8")
    # The four T-251 hands back to the developer, each of which silently
    # produces a module that "works" locally and is broken in production if
    # skipped.
    assert "build_router()" in readme
    assert "autogenerate" in readme
    assert "Permission" in readme
    assert "BUDGETS" in readme


# --- ...and the application cannot see it ------------------------------------


def test_no_module_name_is_a_template_or_underscore_prefixed() -> None:
    # The registry is a closed Literal, so `_template` can't be enabled by
    # accident -- but the underscore convention is what the loader's callers
    # assume, so assert it rather than trusting it.
    for name in get_args(ModuleName):
        assert not name.startswith("_"), f"{name} would collide with the _template convention"


def test_the_template_is_absent_from_the_loaded_modules() -> None:
    loaded = vars(get_modules())
    assert "_template" not in loaded
    assert not any(name.startswith("_") for name in loaded)


def test_no_template_route_is_registered() -> None:
    # iter_route_contexts, not app.routes directly: FastAPI 0.137 keeps
    # included routers lazy (`_IncludedRouter`), so the top-level list would
    # make this pass without ever looking at a real route (same trap noted in
    # test_query_budgets.py).
    contexts = list(iter_route_contexts(app.routes))
    assert contexts  # the traversal found something to check
    for context in contexts:
        assert "__MODULE_NAME__" not in context.path
        endpoint = getattr(context.original_route, "endpoint", None)
        module = getattr(endpoint, "__module__", "")
        assert not module.startswith("app.modules._template"), (
            f"{context.path} is served by {module}"
        )


def test_the_openapi_schema_has_no_trace_of_the_template() -> None:
    # The schema is what the generated TypeScript client is built from
    # (`make gen-client`): a leaked scaffold route would ship to web and
    # mobile, not just to the server. The placeholder is the exact marker --
    # a real module may perfectly well own a schema called `ItemPublic`.
    assert "__MODULE_NAME__" not in json.dumps(app.openapi())


def test_the_models_aggregator_does_not_import_the_template() -> None:
    # app/models/__init__.py is what puts every *real* module's tables on
    # SQLModel.metadata. The scaffold's table must stay off it.
    aggregator = (_BACKEND_DIR / "app" / "models" / "__init__.py").read_text(encoding="utf-8")
    assert "_template" not in aggregator
