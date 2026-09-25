"""Every job name enqueued anywhere in `app/` resolves to a registered worker function.

This is the test that was missing. `app.core.jobs.enqueue_job` takes the job
name as a *string*, and arq resolves that string against
`WorkerSettings.functions` at run time, in the worker process. So a name with
no function behind it is not an ImportError, not a type error, and not
anything mypy or ruff can see: it is a call that succeeds, a job that is
accepted onto the queue, and then a line in a worker log nobody reads.

`hard_delete_stored_file` sat in exactly that state from T-112 until now --
StorageService.delete_file enqueued it on every deletion, and no function by
that name had ever existed. Every deleted file's object stayed in the bucket.

The sweep below reads the names out of the source rather than being a list
someone maintains, because a hand-maintained list of enqueued names would
have the same failure mode as the thing it is checking: it would be right on
the day it was written.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.workers.main import WorkerSettings

APP_ROOT = Path(__file__).resolve().parent.parent / "app"


def _enqueued_job_names() -> set[str]:
    """Every string literal passed as `enqueue_job`'s first argument under app/.

    Deliberately syntactic (an AST walk, not an import): it finds call sites
    in modules this test never imports, including ones behind a module toggle
    that is off in this configuration. A name is only counted when it is a
    literal, which is the only form that can be checked at all -- see
    `test_no_job_is_enqueued_under_a_computed_name` for the other half.
    """
    names: set[str] = set()
    for path in APP_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            attr = func.attr if isinstance(func, ast.Attribute) else None
            plain = func.id if isinstance(func, ast.Name) else None
            if attr != "enqueue_job" and plain != "enqueue_job":
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                names.add(first.value)
    return names


def _registered_job_names() -> set[str]:
    # arq looks a job up by the registered coroutine's __name__ (the
    # attribute @trace_job preserves via functools.wraps), which is what
    # makes the string on the enqueue side and the function on the worker
    # side the same name.
    return {func.__name__ for func in WorkerSettings.functions}


def test_every_enqueued_job_name_is_registered() -> None:
    enqueued = _enqueued_job_names()
    # A guard on the guard: if the AST walk silently stops matching (someone
    # renames the helper, or wraps it), an empty set would make this test
    # pass while checking nothing.
    assert len(enqueued) >= 5, f"expected to find several enqueue sites, found {enqueued}"

    unregistered = enqueued - _registered_job_names()
    assert not unregistered, (
        f"enqueued but not in WorkerSettings.functions: {sorted(unregistered)}. "
        f"arq resolves these names at run time, so this does not fail at import "
        f"or in CI -- it fails as a job that is queued and never runs."
    )


@pytest.mark.parametrize("name", sorted(_enqueued_job_names()))
def test_enqueued_name_resolves(name: str) -> None:
    # The parametrised form of the assertion above, so a failure names the
    # offending job in the test id rather than in a list.
    assert name in _registered_job_names()


def test_no_job_is_enqueued_under_a_computed_name() -> None:
    """Every `enqueue_job` first argument is a literal, so the sweep sees it all.

    An f-string or a variable would be invisible to `_enqueued_job_names`,
    which would quietly narrow this whole file's coverage. If a call site
    ever genuinely needs a computed name, this test is the place to decide
    what to do about it -- not a thing to notice later.
    """
    computed: list[str] = []
    for path in APP_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            attr = func.attr if isinstance(func, ast.Attribute) else None
            plain = func.id if isinstance(func, ast.Name) else None
            if attr != "enqueue_job" and plain != "enqueue_job":
                continue
            # app.core.jobs.enqueue_job's own body forwards its `function`
            # parameter to the arq pool; that is the definition, not a caller.
            if path.name == "jobs.py" and path.parent.name == "core":
                continue
            first = node.args[0]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                computed.append(f"{path.relative_to(APP_ROOT)}:{first.lineno}")

    assert not computed, f"enqueue_job called with a non-literal job name at: {computed}"
