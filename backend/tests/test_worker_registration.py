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
from arq.constants import default_queue_name

from app.core.jobs import queue_for
from app.workers.main import ScanWorkerSettings, WorkerSettings

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


# Every worker class a deployment runs, and so every queue something consumes.
_WORKERS = (WorkerSettings, ScanWorkerSettings)


def _consumed() -> dict[str, set[str]]:
    """queue name -> the job names registered by the worker on that queue.

    arq looks a job up by the registered coroutine's __name__ (the attribute
    @trace_job preserves via functools.wraps), which is what makes the
    string on the enqueue side and the function on the worker side the same
    name.
    """
    return {
        getattr(worker, "queue_name", default_queue_name): {f.__name__ for f in worker.functions}
        for worker in _WORKERS
    }


def _registered_job_names() -> set[str]:
    return {name for names in _consumed().values() for name in names}


def test_every_enqueued_job_name_is_registered() -> None:
    enqueued = _enqueued_job_names()
    # A guard on the guard: if the AST walk silently stops matching (someone
    # renames the helper, or wraps it), an empty set would make this test
    # pass while checking nothing.
    assert len(enqueued) >= 5, f"expected to find several enqueue sites, found {enqueued}"

    unregistered = enqueued - _registered_job_names()
    assert not unregistered, (
        f"enqueued but registered on no worker: {sorted(unregistered)}. "
        f"arq resolves these names at run time, so this does not fail at import "
        f"or in CI -- it fails as a job that is queued and never runs."
    )


@pytest.mark.parametrize("name", sorted(_enqueued_job_names()))
def test_enqueued_name_resolves(name: str) -> None:
    """Registered on *the queue it is sent to* (review R4).

    A name registered on one worker but routed to another queue is the same
    failure as an unregistered one: accepted, and never run.
    """
    queue = queue_for(name)
    assert name in _consumed().get(queue, set()), (
        f"{name} is routed to {queue!r}, whose worker does not register it"
    )


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
