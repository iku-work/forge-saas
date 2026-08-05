"""
T-251: `make new-module NAME=foo` produces a module that is green before it is
touched, and a checklist whose pointers are real.

Three properties, and the middle one is the one that decays quietly. (1) A bad
NAME fails with a message a developer can act on, before anything is written.
(2) The rendered output is complete -- no placeholder survives -- and is
already lint-, format- and type-clean, so `ruff format` inside the generator is
a guard rather than a repair; the moment a template edit makes it a repair,
test_generation_changes_nothing_that_ruff_would_change fails and says so. (3)
The follow-up checklist's file:line pointers resolve against the tree as it is
today, which is the whole reason they are computed instead of typed.

The writing tests run against a scratch backend root (the two templates plus
ruff.toml copied into tmp_path), so the suite never leaves a half-generated
module behind in app/modules/. The pointer test runs against the real one --
that is the tree whose line numbers move.
"""

import ast
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import new_module
from scripts.new_module import (
    MAX_NAME_LENGTH,
    MODEL_NAME_PLACEHOLDER,
    MODULE_NAME_PLACEHOLDER,
    GenerationError,
    follow_ups,
    generate,
    model_name,
    render,
    validate_name,
)

_BACKEND_ROOT = Path(__file__).resolve().parent.parent

# Postgres truncates identifiers past this silently rather than rejecting them,
# which turns a too-long module name into a collision months later instead of
# an error now.
_PG_IDENTIFIER_LIMIT = 63


@pytest.fixture
def scratch(tmp_path: Path) -> Path:
    """A backend root carrying only what the generator reads."""
    root = tmp_path / "backend"
    shutil.copytree(
        _BACKEND_ROOT / "app" / "modules" / "_template",
        root / "app" / "modules" / "_template",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    test_template = root / "tests" / "modules" / "_template_test.py.tmpl"
    test_template.parent.mkdir(parents=True)
    shutil.copy(_BACKEND_ROOT / "tests" / "modules" / "_template_test.py.tmpl", test_template)
    shutil.copy(_BACKEND_ROOT / "ruff.toml", root / "ruff.toml")
    # tests/ and tests/modules/ are real packages in the repo, and ruff's
    # INP001 notices when they aren't: without these the scratch tree would
    # fail a lint the real one passes.
    (root / "tests" / "__init__.py").touch()
    (root / "tests" / "modules" / "__init__.py").touch()
    return root


def _generated_text(root: Path, name: str) -> dict[str, str]:
    module_dir = root / "app" / "modules" / name
    files = {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in module_dir.rglob("*")
        if path.is_file()
    }
    test_file = root / "tests" / "modules" / f"test_{name}.py"
    files[str(test_file.relative_to(root))] = test_file.read_text(encoding="utf-8")
    return files


# --- a bad NAME fails before anything is written ------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("", "NAME is required"),
        ("Foo-Bar", "not a valid module name"),
        ("FooBar", "not a valid module name"),
        ("foo bar", "not a valid module name"),
        ("2fa", "not a valid module name"),
        ("foo__bar", "not a valid module name"),
        ("_template", "not a valid module name"),
        ("auth", "already taken"),  # an existing module directory
        ("file_storage", "already taken"),  # a toggle in ModuleName, not a directory
        ("class", "already taken"),  # a Python keyword
        ("z" * (MAX_NAME_LENGTH + 1), f"the limit is {MAX_NAME_LENGTH}"),
    ],
)
def test_a_bad_name_is_refused_with_a_usable_message(name: str, expected: str) -> None:
    with pytest.raises(GenerationError, match=expected):
        validate_name(name)


@pytest.mark.parametrize("name", ["", "Foo-Bar", "auth", "z" * (MAX_NAME_LENGTH + 1)])
def test_a_refused_name_writes_nothing(name: str, capsys) -> None:
    # Through main(), against the real backend root: this is the path
    # `make new-module NAME=...` takes, and the thing worth proving is that a
    # refusal is inert -- a partially written module in app/modules/ would be
    # picked up by the next `pytest` run whether or not anyone noticed.
    modules = _BACKEND_ROOT / "app" / "modules"
    test_modules = _BACKEND_ROOT / "tests" / "modules"
    before = (
        sorted(p.name for p in modules.iterdir()),
        sorted(p.name for p in test_modules.iterdir()),
    )

    exit_code = new_module.main([name])

    assert exit_code == 2
    assert capsys.readouterr().err.strip()  # the message goes to stderr, not stdout
    assert (
        sorted(p.name for p in modules.iterdir()),
        sorted(p.name for p in test_modules.iterdir()),
    ) == before


def test_a_valid_name_survives_validation() -> None:
    assert validate_name("zztest") == "zztest"
    assert validate_name("zz_test_2") == "zz_test_2"


@pytest.mark.parametrize(
    ("name", "expected"),
    [("invoices", "Invoices"), ("audit_log", "AuditLog"), ("a_b_c", "ABC")],
)
def test_the_model_name_is_the_pascal_case_of_the_module_name(name: str, expected: str) -> None:
    assert model_name(name) == expected


# --- the generated output is complete -----------------------------------------


def test_generation_writes_the_whole_module_and_its_tests(scratch: Path) -> None:
    result = generate("invoices", backend_root=scratch)

    assert result.model == "Invoices"
    assert sorted(path.name for path in result.module_dir.iterdir()) == [
        "README.md",
        "__init__.py",
        "models.py",
        "repository.py",
        "router.py",
        "schemas.py",
        "service.py",
    ]
    assert result.test_file == scratch / "tests" / "modules" / "test_invoices.py"
    assert result.test_file.is_file()


def test_no_placeholder_survives_anywhere_in_the_output(scratch: Path) -> None:
    # The grep the task asks for, over every generated byte rather than a
    # sampled file: one missed occurrence is a module that imports
    # `app.modules.__MODULE_NAME__` and fails at collection.
    generate("audit_log", backend_root=scratch)

    for path, text in _generated_text(scratch, "audit_log").items():
        assert MODULE_NAME_PLACEHOLDER not in text, path
        assert MODEL_NAME_PLACEHOLDER not in text, path


def test_the_substitution_reaches_the_places_that_matter(scratch: Path) -> None:
    generate("audit_log", backend_root=scratch)
    files = _generated_text(scratch, "audit_log")

    models = files[str(Path("app/modules/audit_log/models.py"))]
    assert '__tablename__ = "audit_log_item"' in models
    router = files[str(Path("app/modules/audit_log/router.py"))]
    assert 'prefix="/audit_log"' in router
    service = files[str(Path("app/modules/audit_log/service.py"))]
    assert '"audit_log.item_created"' in service
    tests = files[str(Path("tests/modules/test_audit_log.py"))]
    assert "from app.modules.audit_log.models import Item" in tests
    assert '_PREFIX = "/api/v1/audit_log"' in tests
    readme = files[str(Path("app/modules/audit_log/README.md"))]
    assert "AuditLog" in readme  # __ModelName__, which only appears in prose


def test_pycache_is_not_copied_into_the_new_module(scratch: Path) -> None:
    # The scaffold is imported by tests/test_module_template.py's ruff/mypy
    # runs, so a developer's _template/ almost always has one.
    cache = scratch / "app" / "modules" / "_template" / "__pycache__"
    cache.mkdir()
    (cache / "models.cpython-312.pyc").write_bytes(b"\x00stale")

    result = generate("invoices", backend_root=scratch)

    assert not (result.module_dir / "__pycache__").exists()
    assert all(path.suffix != ".pyc" for path in result.files)


def test_generating_over_an_existing_module_is_refused(scratch: Path) -> None:
    generate("invoices", backend_root=scratch)
    before = _generated_text(scratch, "invoices")

    # "already taken" rather than "already exists": once the directory is
    # there, reserved_names() sees it first -- the two checks overlap here on
    # purpose, and either message tells the developer the same true thing.
    with pytest.raises(GenerationError, match="already taken"):
        generate("invoices", backend_root=scratch)

    assert _generated_text(scratch, "invoices") == before


def test_an_orphan_test_file_alone_is_enough_to_refuse(scratch: Path) -> None:
    # The half-cleaned-up case: someone deleted app/modules/invoices/ but not
    # tests/modules/test_invoices.py. Generating anyway would silently reuse
    # whatever that file had grown into.
    (scratch / "tests" / "modules" / "test_invoices.py").write_text("# hand-written\n")

    with pytest.raises(GenerationError, match="already exists"):
        generate("invoices", backend_root=scratch)

    assert not (scratch / "app" / "modules" / "invoices").exists()


def test_a_failure_partway_through_leaves_nothing_behind(scratch: Path, monkeypatch) -> None:
    # Half a module is worse than none: the next attempt would refuse to write
    # over it, and nothing would say which of the files were real.
    real_write = new_module._write

    def _explode(path: Path, text: str) -> None:
        if path.name == "test_invoices.py":
            raise OSError("disk full")
        real_write(path, text)

    monkeypatch.setattr(new_module, "_write", _explode)

    with pytest.raises(OSError, match="disk full"):
        generate("invoices", backend_root=scratch)

    assert not (scratch / "app" / "modules" / "invoices").exists()
    assert not (scratch / "tests" / "modules" / "test_invoices.py").exists()


# --- ...and it is green as generated ------------------------------------------


def test_generation_changes_nothing_that_ruff_would_change(scratch: Path) -> None:
    """`ruff format` inside the generator is a guard, not a repair.

    Substitution shifts every line carrying the 15-character placeholder, so
    the formatter could in principle want to rewrap one. Today it never does,
    for any name -- and that is worth pinning: if a template edit makes the
    generator start rewriting its own output, the two templates are no longer
    what they claim to be (`ruff format --check`-clean as they sit), and the
    difference this test prints is the line to fix.
    """
    for name in ("z", "z" * MAX_NAME_LENGTH):
        result = generate(name, backend_root=scratch)
        assert result.formatted, "ruff format did not run -- is ruff installed?"

        template_dir = scratch / "app" / "modules" / "_template"
        for path in result.files:
            source = (
                scratch / "tests" / "modules" / "_template_test.py.tmpl"
                if path == result.test_file
                else template_dir / path.relative_to(result.module_dir)
            )
            expected = render(source.read_text(encoding="utf-8"), name)
            assert path.read_text(encoding="utf-8") == expected, path


@pytest.mark.parametrize("length", range(1, MAX_NAME_LENGTH + 1))
def test_no_rendered_line_outgrows_the_line_length_limit(length: int) -> None:
    # Cheap enough to run for every accepted name length, which is the point:
    # a comment in the template that fits at 15 characters of placeholder can
    # blow past 100 columns at 27, and E501 is not something `ruff format`
    # fixes inside a string or a comment.
    name = "z" * length
    sources = [
        *(_BACKEND_ROOT / "app" / "modules" / "_template").glob("*.py"),
        _BACKEND_ROOT / "tests" / "modules" / "_template_test.py.tmpl",
    ]
    for source in sources:
        for number, line in enumerate(
            render(source.read_text(encoding="utf-8"), name).splitlines(), 1
        ):
            assert len(line) <= 100, f"{source.name}:{number} is {len(line)} columns at NAME={name}"


def test_the_longest_name_still_fits_postgres_identifier_limit() -> None:
    """Re-derives MAX_NAME_LENGTH from the template instead of trusting it.

    The limit exists because of one line in models.py -- the composite index's
    name. If that index is ever renamed or a longer one is added, this fails
    rather than letting the generator hand out names Postgres will truncate
    into each other.
    """
    name = "z" * MAX_NAME_LENGTH
    models = render(
        (_BACKEND_ROOT / "app" / "modules" / "_template" / "models.py").read_text(encoding="utf-8"),
        name,
    )
    # Every string constant that could be a SQL identifier -- lowercase, no
    # spaces -- which is exactly what excludes the docstring the name also
    # appears in.
    identifiers = [
        node.value
        for node in ast.walk(ast.parse(models))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and name in node.value
        and re.fullmatch(r"[a-z0-9_]+", node.value)
    ]
    assert identifiers, "models.py no longer builds any identifier from the module name"
    for identifier in identifiers:
        assert len(identifier) <= _PG_IDENTIFIER_LIMIT, (
            f"{identifier!r} is {len(identifier)} characters -- lower MAX_NAME_LENGTH"
        )


def test_the_generated_module_is_lint_and_type_clean(scratch: Path) -> None:
    # ruff only: mypy would need `app` importable from the scratch root, which
    # is what the CI smoke (a real `make new-module` in a real checkout) covers
    # instead. Here the point is that the *rendered* files are clean, and ruff
    # reads them without resolving a single import.
    result = generate("invoices", backend_root=scratch)
    targets = [str(path) for path in result.files if path.suffix == ".py"]

    for argv in (["check"], ["format", "--check"]):
        completed = subprocess.run(  # noqa: S603 — fixed argv, no shell
            [sys.executable, "-m", "ruff", *argv, "--config", str(scratch / "ruff.toml"), *targets],
            cwd=scratch,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr


# --- the checklist points at real lines ---------------------------------------


def test_every_shared_pointer_resolves_in_the_real_tree() -> None:
    """The half of the checklist that rots: lines in files this task doesn't own.

    A pointer that lost its anchor degrades to a bare path rather than a wrong
    line -- silently useful-looking and useless. This is what notices.
    """
    repo_root = _BACKEND_ROOT.parent
    steps = follow_ups("zztest")
    shared = [pointer for step in steps for pointer in step.pointers if "zztest" not in pointer]
    assert len(shared) >= 9  # permissions x2, routing x4, migrations x4, budgets

    for pointer in shared:
        assert ":" in pointer, f"{pointer} lost its anchor -- the line it named is gone"
        relative, _, line_number = pointer.rpartition(":")
        path = repo_root / relative
        assert path.is_file(), pointer
        lines = path.read_text(encoding="utf-8").splitlines()
        assert 1 <= int(line_number) <= len(lines), pointer
        assert lines[int(line_number) - 1].strip(), pointer


def test_the_module_local_pointers_resolve_in_what_was_just_generated(scratch: Path) -> None:
    generate("invoices", backend_root=scratch)

    module_pointers = [
        pointer
        for step in follow_ups("invoices", backend_root=scratch)
        for pointer in step.pointers
        if "app/modules/invoices" in pointer.replace("\\", "/")
    ]
    assert len(module_pointers) == 6  # four renames, the permissions, the limit

    for pointer in module_pointers:
        relative, _, line_number = pointer.rpartition(":")
        assert line_number.isdigit(), f"{pointer} did not resolve to a line"
        lines = (scratch.parent / relative).read_text(encoding="utf-8").splitlines()
        assert lines[int(line_number) - 1].strip()


def test_the_printed_checklist_covers_what_the_readme_covers(scratch: Path, capsys) -> None:
    # Same four the README's checklist is asserted on in test_module_template.py:
    # each one silently produces a module that "works" locally and is broken in
    # production if skipped.
    result = generate("invoices", backend_root=scratch)
    new_module._report(result, follow_ups("invoices", backend_root=scratch))

    printed = capsys.readouterr().out
    for expected in ("build_router()", "autogenerate", "Permission", "BUDGETS", "gen-client"):
        assert expected in printed
    assert "README.md" in printed  # the long form, with the reasoning
