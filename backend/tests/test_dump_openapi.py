"""
T-313: the OpenAPI document `@forge/api-client` is generated from.

The contract this file defends is one sentence: **the generated client is the
union of every module's routes, whatever `backend/product/config.py` says.**
`apps/web` gates modules at runtime from `/api/v1/config` and imports its
generated symbols unconditionally -- one bundle serves every configuration --
so a client generated from *this* product's schema is missing exactly the
symbols a default-off module's UI needs (`public_api`, `outbound_webhooks`),
which is what left T-311's last box unticked.

The script runs the app in a subprocess rather than in this one, and that is
not squeamishness: it rebinds `product.config.MODULES` and then imports
`app.main`, which registers routers off the `lru_cache`d `get_modules()` at
import time. Doing that inside the test process would leave every later test
looking at an app assembled from a configuration the product does not have.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
SCRIPT = BACKEND_DIR / "scripts" / "dump_openapi.py"


@pytest.fixture(scope="module")
def dumped_schema(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    out = tmp_path_factory.mktemp("openapi") / "openapi.json"
    result = subprocess.run(  # noqa: S603 -- fixed argv, no shell
        [sys.executable, str(SCRIPT), "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(out.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_the_dump_contains_every_optional_modules_routes(dumped_schema: dict[str, Any]) -> None:
    """One assertion per module that ships **off** in this product.

    These are the routes that do not exist in this process at all, which is
    the whole point: if this test can see them, so can the client generator,
    and so can `apps/web`'s import statements.
    """
    paths = set(dumped_schema["paths"])
    assert "/api/v1/api-keys" in paths  # public_api, default off
    assert "/api/v1/webhooks/endpoints" in paths  # outbound_webhooks, default off
    # ... and the always-on ones are still there, i.e. nothing was traded away
    # for the modules above.
    assert "/api/v1/auth/login" in paths
    assert "/api/v1/orgs" in paths


def test_the_dump_is_a_superset_of_what_this_configuration_serves(
    dumped_schema: dict[str, Any],
) -> None:
    """A regeneration must never *delete* a symbol `apps/web` imports.

    The failure this pins is the one CI's module-matrix job used to carry a
    comment apologising for: generate from a smaller configuration and
    type-check fails on the template's design rather than on anything the
    toggle got wrong. A superset can never do that.
    """
    from app.main import app  # noqa: PLC0415 -- the in-process app, at this product's config

    live = set(app.openapi()["paths"])
    assert live <= set(dumped_schema["paths"])


def test_the_document_is_written_deterministically(
    dumped_schema: dict[str, Any], tmp_path: Path
) -> None:
    """Two dumps of the same tree are byte-identical.

    `packages/api-client/src/generated/` is gitignored and regenerated in
    seven CI jobs; a document whose key order wandered between them would
    make the client's diff noise rather than signal, and would defeat every
    build cache downstream of it.
    """
    out = tmp_path / "again.json"
    subprocess.run(  # noqa: S603 -- fixed argv, no shell
        [sys.executable, str(SCRIPT), "--out", str(out)], check=True, cwd=REPO_ROOT
    )
    assert json.loads(out.read_text(encoding="utf-8")) == dumped_schema
    assert (
        out.read_text(encoding="utf-8")
        == json.dumps(dumped_schema, indent=2, sort_keys=True) + "\n"
    )


def test_the_client_generator_reads_the_dumped_file_rather_than_a_url() -> None:
    """The other half of the contract, and the half a Python test can still
    hold: `openapi-ts.config.ts` must not go back to `http://localhost:8000`.

    A URL input silently reintroduces "the client is a picture of whatever
    configuration happened to be running", which is the bug this whole
    arrangement exists to remove.
    """
    config = (REPO_ROOT / "packages" / "api-client" / "openapi-ts.config.ts").read_text(
        encoding="utf-8"
    )
    assert 'input: "./openapi.json"' in config
    # The *input* must not be a URL. Scoped to the declaration rather than to
    # the whole file, because the file's own comment names the URL this
    # replaced -- and that comment is the reason anyone would know not to put
    # it back.
    inputs = [line for line in config.splitlines() if line.strip().startswith("input:")]
    assert inputs == ['  input: "./openapi.json",']
