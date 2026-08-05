"""
`make new-module NAME=foo` (T-251): turns backend/app/modules/_template into a
real module instead of copying it.

Generation, not `cp -r`. The two placeholders T-250 left in the scaffold are
substituted everywhere they occur (`__MODULE_NAME__` in table/index names,
router prefix and tags, audit actions, comments and the test template's import
paths; `__ModelName__` in prose), the test template becomes
`tests/modules/test_<name>.py`, and the result is run through `ruff format`
before it is handed over. The template as it stands renders format-clean for
every name this accepts (test_new_module_script.py sweeps all of them), so
that last step buys nothing today -- it is there because the placeholder is
15 characters wide and a future edit to a line carrying it could easily be
format-stable at 15 and not at 3, and a generated module that fails CI's
`ruff format --check` on its first commit poisons every module after it.

What this deliberately does *not* do is register the module. The router entry,
the module toggle, the migration, the query budget and the permission pair are
each a decision belonging to whoever is adding the module -- and each is a
place where a wrong guess is worse than an omission. They are printed instead,
as `file:line` pointers resolved against the real tree at generation time, so
the list cannot rot the way a hand-maintained one does.
"""

from __future__ import annotations

import argparse
import ast
import keyword
import re
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent

MODULE_NAME_PLACEHOLDER = "__MODULE_NAME__"
MODEL_NAME_PLACEHOLDER = "__ModelName__"

TEMPLATE_DIR = Path("app") / "modules" / "_template"
TEST_TEMPLATE = Path("tests") / "modules" / "_template_test.py.tmpl"
MODULES_DIR = Path("app") / "modules"
TESTS_DIR = Path("tests") / "modules"

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")

# 27, not a round number: the longest identifier the scaffold generates is
# models.py's index, `ix_<name>_item_org_id_status_created_at_id` -- 36
# characters of fixed text around the name. Postgres truncates identifiers to
# 63 bytes *silently*, so a longer name would not fail, it would collide with
# the next long name's index. tests/test_new_module_script.py re-derives this
# from the rendered template rather than trusting the arithmetic here.
MAX_NAME_LENGTH = 27


class GenerationError(Exception):
    """A usable message for the developer at the terminal, not a stack trace."""


@dataclass(frozen=True)
class Generated:
    name: str
    model: str
    module_dir: Path
    test_file: Path
    files: tuple[Path, ...]
    formatted: bool


@dataclass(frozen=True)
class FollowUp:
    title: str
    detail: str
    pointers: tuple[str, ...]


def model_name(name: str) -> str:
    """`invoices` -> `Invoices`, `audit_log` -> `AuditLog`."""
    return "".join(part.capitalize() for part in name.split("_"))


def render(source: str, name: str) -> str:
    return source.replace(MODULE_NAME_PLACEHOLDER, name).replace(
        MODEL_NAME_PLACEHOLDER, model_name(name)
    )


def _literal_values(path: Path, target: str) -> set[str]:
    """The strings in a module-level `X = Literal["a", "b"]`, read without importing.

    Parsed rather than imported so the generator stays runnable from a bare
    checkout (no installed `app` package, no .env) and so tests can point it
    at a scratch tree.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == target for t in node.targets):
            continue
        if not isinstance(node.value, ast.Subscript):
            continue
        elements = node.value.slice
        items = elements.elts if isinstance(elements, ast.Tuple) else [elements]
        return {
            item.value
            for item in items
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        }
    return set()


def reserved_names(backend_root: Path = BACKEND_ROOT) -> set[str]:
    """Everything `NAME` may not be, gathered from the tree rather than a list here."""
    existing = {
        path.name
        for path in (backend_root / MODULES_DIR).glob("*")
        if path.is_dir() and path.name != "__pycache__"
    }
    toggles = _literal_values(backend_root / "app" / "core" / "modules.py", "ModuleName")
    return existing | toggles | set(keyword.kwlist) | set(keyword.softkwlist)


def validate_name(name: str, backend_root: Path = BACKEND_ROOT) -> str:
    if not name:
        raise GenerationError("NAME is required.\nUsage: make new-module NAME=invoices")
    if not NAME_PATTERN.match(name):
        raise GenerationError(
            f"{name!r} is not a valid module name.\n"
            "Use snake_case: lowercase letters and digits, single underscores "
            "between words, starting with a letter -- e.g. NAME=invoices or "
            "NAME=audit_log.\n"
            "It becomes a Python package name, a URL prefix and part of a "
            "table name, and all three have to accept it."
        )
    if len(name) > MAX_NAME_LENGTH:
        raise GenerationError(
            f"{name!r} is {len(name)} characters; the limit is {MAX_NAME_LENGTH}.\n"
            "The module name is part of the scaffold's index name "
            f"(ix_{name}_item_org_id_status_created_at_id), and Postgres "
            "truncates identifiers to 63 bytes silently rather than failing."
        )
    if name in reserved_names(backend_root):
        raise GenerationError(
            f"{name!r} is already taken -- it is an existing module directory, a "
            "module toggle in app/core/modules.py, or a Python keyword.\n"
            "Pick another name, or edit the existing module in place."
        )

    module_dir = backend_root / MODULES_DIR / name
    test_file = backend_root / TESTS_DIR / f"test_{name}.py"
    for path in (module_dir, test_file):
        if path.exists():
            raise GenerationError(
                f"{path} already exists.\n"
                "Nothing was written. Delete it first if you meant to regenerate."
            )
    return name


def _template_sources(backend_root: Path) -> list[Path]:
    template_dir = backend_root / TEMPLATE_DIR
    if not template_dir.is_dir():
        raise GenerationError(f"{template_dir} is missing -- nothing to generate from.")
    return sorted(
        path
        for path in template_dir.rglob("*")
        if path.is_file()
        and path.suffix != ".pyc"
        and "__pycache__" not in path.relative_to(template_dir).parts
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n" explicitly: on Windows the default would write CRLF, and the
    # generated module would land in git with line endings no other file in
    # backend/ has.
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _ruff_format(backend_root: Path, paths: list[Path]) -> bool:
    """Best-effort. A generated module that ruff can't format is still usable."""
    config = backend_root / "ruff.toml"
    argv = [sys.executable, "-m", "ruff", "format"]
    if config.is_file():
        # Pinned rather than discovered: the generator may be pointed at a tree
        # that has no ruff.toml above it, and ruff's built-in default
        # (line-length 88) formats differently from this project's 100.
        argv += ["--config", str(config)]
    try:
        result = subprocess.run(  # noqa: S603 — fixed argv, no shell
            [*argv, *(str(path) for path in paths)],
            cwd=backend_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def generate(name: str, backend_root: Path = BACKEND_ROOT) -> Generated:
    validate_name(name, backend_root)

    template_dir = backend_root / TEMPLATE_DIR
    module_dir = backend_root / MODULES_DIR / name
    test_file = backend_root / TESTS_DIR / f"test_{name}.py"
    sources = _template_sources(backend_root)

    # Both templates are checked before anything is written: rendering only the
    # module would leave the router prefix pointing at routes no test exercises,
    # and rendering only the tests would leave them importing a module that
    # doesn't exist.
    template = backend_root / TEST_TEMPLATE
    if not template.is_file():
        raise GenerationError(f"{template} is missing -- nothing to generate tests from.")

    written: list[Path] = []
    try:
        for source in sources:
            target = module_dir / source.relative_to(template_dir)
            _write(target, render(source.read_text(encoding="utf-8"), name))
            written.append(target)

        _write(test_file, render(template.read_text(encoding="utf-8"), name))
        written.append(test_file)
    except Exception:
        # Half a module is worse than none: the next run would refuse to
        # write over it and the developer would have to work out which files
        # were real.
        shutil.rmtree(module_dir, ignore_errors=True)
        test_file.unlink(missing_ok=True)
        raise

    formatted = _ruff_format(backend_root, [path for path in written if path.suffix == ".py"])
    return Generated(
        name=name,
        model=model_name(name),
        module_dir=module_dir,
        test_file=test_file,
        files=tuple(written),
        formatted=formatted,
    )


def _locate(repo_root: Path, relative: str, needle: str) -> str:
    """`path:line` for the first line containing `needle`, or the bare path."""
    try:
        lines = (repo_root / relative).read_text(encoding="utf-8").splitlines()
    except OSError:
        return relative
    for number, line in enumerate(lines, start=1):
        if needle in line:
            return f"{relative}:{number}"
    return relative


def follow_ups(name: str, backend_root: Path = BACKEND_ROOT) -> list[FollowUp]:
    """The manual steps, with pointers resolved against the tree as it is now.

    Deliberately the same eight as the generated README's checklist -- that is
    the long form with the reasoning, this is the one you can paste into an
    editor. Keep them in step.
    """
    repo = backend_root.parent
    module = f"backend/app/modules/{name}"
    model = model_name(name)

    return [
        FollowUp(
            title=f"Rename Item to {model}",
            detail=(
                "The scaffold is built around one generic entity. It renames in "
                "five places: the class, its __tablename__ and index name, the "
                "repository, the service, and the three schemas."
            ),
            pointers=(
                _locate(repo, f"{module}/models.py", "class Item("),
                _locate(repo, f"{module}/repository.py", "class ItemRepository("),
                _locate(repo, f"{module}/service.py", "class ItemService"),
                _locate(repo, f"{module}/schemas.py", "class ItemCreateRequest("),
            ),
        ),
        FollowUp(
            title="Give the module its own permissions",
            detail=(
                f"router.py ships gated on existing permissions so {name} is never "
                f'open. Add {name.upper()}_READ = "{name}:read" and '
                f'{name.upper()}_MANAGE = "{name}:manage" to Permission, grant '
                "them per role in ROLE_PERMISSIONS, then repoint the two "
                "constants. Both are code: a deploy, not a migration."
            ),
            pointers=(
                _locate(repo, "backend/app/modules/auth/permissions.py", "class Permission("),
                _locate(repo, "backend/app/modules/auth/permissions.py", "ROLE_PERMISSIONS: dict"),
                _locate(repo, f"{module}/router.py", "READ_PERMISSION ="),
            ),
        ),
        FollowUp(
            title="Register the router",
            detail=(
                "Add the router to build_router()'s always-on block, or behind an "
                f"`if modules.{name}.enabled:` branch -- in which case {name} also "
                "needs an entry in ModuleName/EnabledModules and in both product "
                "configs."
            ),
            pointers=(
                _locate(repo, "backend/app/api/v1/__init__.py", "def build_router("),
                _locate(repo, "backend/app/core/modules.py", "ModuleName = Literal["),
                _locate(repo, "backend/product/config.py", "MODULES = EnabledModules("),
                _locate(repo, "product.config.ts", "modules:"),
            ),
        ),
        FollowUp(
            title="Create the migration",
            detail=(
                f'cd backend && alembic revision --autogenerate -m "{name}_tables"\n'
                "Autogenerate diffs against the models alembic imports by hand, so "
                "it proposes nothing until the module's models.py is imported in "
                "load_models_for_migration() -- and the app needs the same import "
                "in app/models/__init__.py. If the module is optional, its table "
                "also belongs in MODULE_TABLES and MODULE_BRANCHES, or disabling "
                "it will not remove its tables. Read the generated script before "
                "applying it."
            ),
            pointers=(
                _locate(repo, "backend/alembic/env.py", "def load_models_for_migration("),
                _locate(repo, "backend/app/models/__init__.py", "from app.modules.audit"),
                _locate(repo, "backend/alembic/env.py", "MODULE_TABLES: dict"),
                _locate(repo, "backend/app/core/migrations.py", "MODULE_BRANCHES: dict"),
            ),
        ),
        FollowUp(
            title="Fill in the query budget",
            detail=(
                f"Every list_* route needs a row in BUDGETS -- add one for "
                f"GET /api/v1/{name}. The suite fails the build on a list route "
                "without one, which is how N+1s stay caught (T-243)."
            ),
            pointers=(_locate(repo, "backend/tests/test_query_budgets.py", "BUDGETS: dict"),),
        ),
        FollowUp(
            title="Decide the real limit",
            detail=(
                "MAX_ACTIVE_ITEMS is a plain constant so a brand-new module is not "
                "coupled to billing. If this module is something you sell by the "
                "seat, row or gigabyte, read the ceiling off the org's plan instead "
                "-- app.modules.storage.service._check_quota is that shape end to "
                "end."
            ),
            pointers=(_locate(repo, f"{module}/service.py", "MAX_ACTIVE_ITEMS ="),),
        ),
        FollowUp(
            title="Regenerate the API client",
            detail=(
                "make gen-client -- the new endpoints appear in @forge/api-client "
                "for web and mobile. Nothing typed can call them before this."
            ),
            pointers=(),
        ),
        FollowUp(
            title="Run the generated tests",
            detail=(
                f"cd backend && pytest tests/modules/test_{name}.py\n"
                "They are green as generated, before any of the steps above. The "
                "three at the top are the non-negotiables (cross-tenant isolation, "
                "the RBAC gate, cursor pagination) -- keep them green rather than "
                "adapting them."
            ),
            pointers=(f"backend/tests/modules/test_{name}.py",),
        ),
    ]


def _report(generated: Generated, steps: list[FollowUp]) -> None:
    module_files = len(generated.files) - 1
    print(f"Generated backend/app/modules/{generated.name}/ ({module_files} files)")
    print(f"Generated backend/tests/modules/test_{generated.name}.py")
    if not generated.formatted:
        print("! ruff format did not run over the output -- run `ruff format .` in backend/")
    print()
    print("Not done for you, in the order they hurt least:")
    for index, step in enumerate(steps, start=1):
        print()
        print(f"  {index}. {step.title}")
        for paragraph in step.detail.splitlines():
            # Wrapped per line, not over the whole detail: a paragraph that
            # starts with a command (`cd backend && alembic ...`) has to stay
            # on its own line to be copy-pasteable.
            print(
                textwrap.fill(
                    paragraph, width=76, initial_indent="     ", subsequent_indent="     "
                )
            )
        for pointer in step.pointers:
            print(f"     -> {pointer}")
    print()
    print(f"backend/app/modules/{generated.name}/README.md has the same list with the reasoning.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="make new-module NAME=<name>",
        description="Generate a module from backend/app/modules/_template.",
    )
    parser.add_argument("name", nargs="?", default="", help="snake_case module name")
    args = parser.parse_args(argv)

    try:
        generated = generate(args.name)
    except GenerationError as error:
        print(str(error), file=sys.stderr)
        return 2

    _report(generated, follow_ups(generated.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
