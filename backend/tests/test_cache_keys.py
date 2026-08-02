"""
T-240's "no inline cache-key strings (grep check)" done-when, as a test
rather than a habit: every cache key in the backend is built by
app.core.cache_keys, so the invalidation matrix in that module's docstring
can stay a complete list of what has to be busted where.

The sweep greps (via the AST, so a key inside a comment or a docstring can't
trip it) for an f-string whose first segment is a namespace-style literal --
f"user:{...}", f"storage:total_bytes:{...}" -- which is the exact shape a
cache key takes when someone builds one at a call site instead.
"""

import ast
import inspect
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.core import cache_keys

_APP_ROOT = Path(__file__).resolve().parent.parent / "app"
_KEYS_MODULE = _APP_ROOT / "core" / "cache_keys.py"

# Redis keys that are deliberately not cache keys, so they don't belong in
# cache_keys.py and have no invalidation matrix to keep honest:
_ALLOWED = {
    # limits' identity key for the rate limiter -- a bucket, not a cached row.
    "app/middleware/rate_limit.py",
    # single-token revocation blocklist (T-142) and the SSE pub/sub channel +
    # connection-slot counter (T-104): live state, never a copy of a DB row.
    "app/core/token_blocklist.py",
    "app/modules/notifications/sse.py",
    # the scheduler's distributed lock keys (T-034).
    "app/workers/scheduler.py",
    # a per-target-address rate-limit counter for the change-email flow
    # (T-226) -- an INCR/EXPIRE bucket, same category as the limiter above.
    "app/modules/auth/service.py",
    # NotificationService's dunning idempotency key (T-130) -- a marker
    # carried into a DB column, not a Redis key at all.
    "app/workers/jobs/billing.py",
}


def _is_namespace_literal(value: str) -> bool:
    head = value.partition(":")[0]
    if not head.isidentifier() or not head.islower() or not value.endswith(":"):
        return False
    # "i18n: missing key {x}" is a log line; "https://{domain}/billing" is a
    # URL -- neither is a key, and both start with an identifier and a colon.
    return not (any(c.isspace() for c in value) or "//" in value)


def _inline_key_literals(source: str) -> list[str]:
    """Every f-string in `source` shaped like a hand-built cache key: a
    namespace-ish literal head, and an interpolation (the id) at the end.
    A trailing literal -- f"virustotal:{n}_engines_flagged" -- means it is
    prose or an identifier, not a key."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.JoinedStr) or not node.values:
            continue
        first, last = node.values[0], node.values[-1]
        if (
            isinstance(first, ast.Constant)
            and isinstance(first.value, str)
            and isinstance(last, ast.FormattedValue)
            and _is_namespace_literal(first.value)
        ):
            found.append(first.value)
    return found


def test_the_detector_catches_a_hand_built_key() -> None:
    # Guards against the sweep below passing vacuously, e.g. after a refactor
    # that changes how f-strings parse.
    assert _inline_key_literals('key = f"user:{user_id}"') == ["user:"]
    assert _inline_key_literals('key = f"storage:total_bytes:{org_id}"') == ["storage:total_bytes:"]
    assert _inline_key_literals('msg = f"took {ms}ms"') == []


@pytest.mark.parametrize(
    "path",
    sorted(p for p in _APP_ROOT.rglob("*.py") if p != _KEYS_MODULE),
    ids=lambda p: str(p.relative_to(_APP_ROOT.parent)).replace("\\", "/"),
)
def test_no_module_builds_a_cache_key_inline(path: Path) -> None:
    relative = str(path.relative_to(_APP_ROOT.parent)).replace("\\", "/")
    if relative in _ALLOWED:
        pytest.skip(f"{relative} holds non-cache Redis keys (see _ALLOWED)")

    offenders = _inline_key_literals(path.read_text(encoding="utf-8"))
    assert not offenders, (
        f"{relative} builds {offenders} inline -- add a builder to "
        f"app.core.cache_keys and its row to that module's invalidation matrix"
    )


def _call_builder(builder: Any) -> str:  # noqa: ANN401 -- any cache-key builder
    """Calls a key builder with a plausible value per parameter name, so a
    newly added builder is covered by the namespace check below without
    anyone remembering to list it here."""
    args = [
        uuid4() if parameter.annotation in (UUID, "UUID") else "sample"
        for parameter in inspect.signature(builder).parameters.values()
    ]
    return str(builder(*args))


def test_every_builder_uses_a_declared_namespace() -> None:
    builders = [
        member
        for name, member in inspect.getmembers(cache_keys, inspect.isfunction)
        if not name.startswith("_")
    ]
    assert builders  # the module still has builders at all

    for builder in builders:
        key = _call_builder(builder)
        namespace = key.split(":")[0]
        assert namespace in cache_keys.CACHE_NAMESPACES, (
            f"{builder.__name__} builds {key!r}, whose namespace is not in CACHE_NAMESPACES"
        )
