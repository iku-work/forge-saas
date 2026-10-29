"""
`pnpm --filter @forge/api-client generate` (T-313): the OpenAPI document the
TypeScript client is generated from, written offline with **every module
enabled**.

## Why this exists at all

`packages/api-client/src/generated/` used to be produced from a *live*
`/openapi.json`, which meant the client only ever contained the routes the
committed `backend/product/config.py` happened to enable. That is wrong in
both directions:

  * A module that ships **off** (`public_api`, `outbound_webhooks`,
    `enterprise_sso`) has no generated symbols, so `apps/web` cannot import
    them -- even though the SPA gates modules at *runtime* from
    `/api/v1/config` and one bundle is meant to serve every configuration.
    T-311 left its "regenerate the client" box unticked for exactly this
    reason and handed the decision here.
  * Generating from a *smaller* configuration (CI's module-matrix
    combination B, say) deletes symbols the app imports unconditionally, so
    type-check fails on the template's design rather than on anything the
    toggle got wrong. CI's `module-matrix` job carried a comment apologising
    for that.

The answer to both is the same: the generated client is the **union** of
every configuration, and the runtime gate is what decides whether a page is
reachable. A call into a disabled module's route 404s, which is precisely
what the module toggle promises -- and a page that could make that call is
already behind `useModules()`.

## Why offline rather than against a running server

Nothing about producing a schema needs a database, a Redis or a port. Booting
uvicorn for it meant every frontend job in CI waited on a health-poll loop it
did not otherwise need, and `make gen-client` needed Docker. Importing
`app.main` and calling `app.openapi()` is the same document, deterministically,
in about a second -- and it works when `ENVIRONMENT != "dev"`, where
`openapi_url` is deliberately `None` (§ ASVS V14.3.2) and the live route does
not exist at all.

## Notes for whoever changes this next

* The module override has to happen **before** `app.main` is imported:
  `build_router()` runs at that module's import time, and `get_modules()` is
  `lru_cache`d.
* Settings are read from `backend/.env` (pydantic-settings resolves
  `env_file` relative to the *working directory*, hence the chdir), falling
  back to `.env.test` on a fresh checkout that has not copied one yet. No
  value here reaches the document; they only have to exist so `Settings`
  constructs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import fields
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dotenv import dotenv_values

if TYPE_CHECKING:
    from app.core.modules import ModuleName

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
DEFAULT_OUTPUT = REPO_ROOT / "packages" / "api-client" / "openapi.json"


def _load_env_fallback() -> None:
    """Populate `os.environ` from `.env.test` when `backend/.env` is absent.

    A fresh checkout has no `.env` (it is gitignored), and `Settings` refuses
    to construct without `SECRET_KEY`/`ENCRYPTION_KEY`/`DATABASE_URL`. The
    committed `.env.test` holds dummy values for all of them, and every one
    of them is irrelevant to a schema dump -- so falling back to it turns
    "clone, `pnpm install`, `pnpm lint`" into something that works, which it
    previously did not (the client could not build without a running backend).

    Values already in the real environment win, so CI can still override.
    Injected through `os.environ` rather than by pointing `env_file` at
    another file on purpose: `Settings` declares `extra="forbid"`, which
    applies to dotenv keys but not to the ambient environment, and `.env.test`
    is allowed to carry keys this app does not read.
    """
    if (BACKEND_DIR / ".env").exists():
        return
    fallback = BACKEND_DIR / ".env.test"
    if not fallback.exists():
        return
    # `dotenv_values`, not a `line.partition("=")` loop: `.env.test` carries
    # multi-line quoted PEM keys, and every base64 line inside one contains an
    # `=` of its own. A naive parser sets JWT_PRIVATE_KEY to the first line of
    # the key and invents a dozen environment variables named after base64
    # fragments -- which produces a Settings that constructs and a schema that
    # is subtly wrong. python-dotenv is already a dependency; pydantic-settings
    # is what pulls it in, and it is the same parser `env_file` uses.
    for name, value in dotenv_values(fallback).items():
        if value is not None:
            os.environ.setdefault(name, value)
    # .env.test carries no keys any more; any valid ones do for a schema.
    from scripts.throwaway_keys import generate  # noqa: PLC0415 -- must follow the sys.path setup

    for name, value in generate().items():
        os.environ.setdefault(name, value)


def _enable_every_module() -> None:
    """Rewrite `product.config.MODULES` so every toggle is on.

    `declared_requirements()` is the source of the `requires` edges (see its
    docstring: a config that spells a module out drops them), so rebuilding
    each entry with its declared edges keeps `validate_modules()` meaningful
    rather than merely quiet -- an all-on configuration satisfies every edge
    by construction, and a future edge pointing at a module that does not
    exist would still fail here.
    """
    import product.config as product_config  # noqa: PLC0415 -- must follow the sys.path setup
    from app.core.modules import EnabledModules, ModuleConfig  # noqa: PLC0415

    requirements = _declared_requirements()
    product_config.MODULES = EnabledModules(
        **{
            module_field.name: ModuleConfig(
                enabled=True, requires=list(requirements.get(module_field.name, ()))
            )
            for module_field in fields(EnabledModules)
        }
    )


def _declared_requirements() -> dict[str, tuple[ModuleName, ...]]:
    from app.core.modules import declared_requirements  # noqa: PLC0415

    return declared_requirements()


def build_schema() -> dict[str, Any]:
    """The OpenAPI document for an all-modules-on build of this app."""
    os.chdir(BACKEND_DIR)
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    _load_env_fallback()
    _enable_every_module()

    from app.main import app  # noqa: PLC0415 -- imports the router; must follow the override

    schema: dict[str, Any] = app.openapi()
    return schema


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="where to write the document (default: packages/api-client/openapi.json)",
    )
    args = parser.parse_args(argv)

    schema = build_schema()
    # Resolved before the chdir in build_schema() would confuse a relative
    # --out, and after it for the default, which is already absolute.
    out: Path = args.out if args.out.is_absolute() else Path.cwd() / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {len(schema.get('paths', {}))} paths to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
