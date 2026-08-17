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

import re
from pathlib import Path
from typing import get_args

from app.core.modules import ModuleName
from product.config import MODULES

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PRODUCT_CONFIG_TS = _REPO_ROOT / "product.config.ts"

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


def _typescript_modules() -> dict[str, bool]:
    source = _PRODUCT_CONFIG_TS.read_text(encoding="utf-8")
    block = _MODULES_BLOCK.search(source)
    assert block is not None, f"no `modules: {{...}}` block found in {_PRODUCT_CONFIG_TS}"
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
