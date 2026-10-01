"""
T-066 / T-291: module-toggle smoke test matrix.

test_api_router.py already proves a disabled module's routes 404, and
test_conditional_migrations.py already proves a disabled module's tables
never get created -- each in isolation. This file is the integration check
the spec asks for: that flipping one switch in product.config.py removes a
module's HTTP surface *and* its DB tables together, the way a product author
actually experiences the toggle.

T-291 turned the loose "these routes should be gone" lists into MODULE_SURFACE
below: one declaration per module of everything its toggle removes. Every case
then asserts both directions -- a disabled module's whole surface is gone, and
nothing *else* is -- so a route quietly moving between modules, or a surface
entry going stale, fails here rather than in production. The set of modules
declared there is checked against `ModuleName` itself, which is what makes
"the matrix covers all fourteen modules" a fact rather than a claim.

What this file cannot reach is everything outside the Python process: the
frontend build, a real `alembic upgrade` from zero against an actual
product/config.py on disk, app boot as a subprocess. That is CI's
`module-matrix` job (T-291), which runs three whole-repo combinations; the
fixtures it copies live in tests/fixtures/module_configs/. The two are
deliberately complementary -- run inside that job, this file is also what
turns combination B red if someone unconditionalises a router import in
build_router().

Each scenario gets its own Postgres container (mirrors
test_conditional_migrations.py's `real_config` fixture) since each drives a
fresh `alembic upgrade` under a distinct module configuration that must not
leak into the session-scoped `engine` fixture other test files share.
"""

import asyncio
from collections.abc import Generator
from dataclasses import replace
from typing import NamedTuple, get_args

import asyncpg
import pytest
from alembic.config import Config
from fastapi import FastAPI
from fastapi.routing import iter_route_contexts

from app.api.v1 import build_router
from app.core import migrations as migration_runner
from app.core import module_loader
from app.core.config import get_settings
from app.core.modules import EnabledModules, ModuleConfig, ModuleName
from product import config as product_config
from tests._database import DatabaseHandle


class ModuleSurface(NamedTuple):
    """Everything one module's toggle removes, in one place.

    `routes` are exact paths, except an entry ending in `/*`, which means that
    path and everything under it. `tables` mirrors alembic/env.py's
    MODULE_TABLES (that copy is what keeps autogenerate honest; this one is
    what the assertions below read). `note` explains a surface that is empty
    on purpose -- see test_an_empty_surface_says_why.
    """

    routes: tuple[str, ...] = ()
    tables: tuple[str, ...] = ()
    note: str = ""


# Empty, and that is the point it has been making since T-290: a module whose
# surface is declared before the code exists cannot ship a route or a table
# without a matching toggle assertion. `public_api` came off this set in
# T-310, `enterprise_sso` in T-331 -- each time because
# test_a_pending_surface_is_retired_once_its_module_ships went red the day the
# router appeared, which is exactly what it is for. A future module declared
# ahead of its code goes back in here.
_SURFACE_PENDING: frozenset[ModuleName] = frozenset()

MODULE_SURFACE: dict[ModuleName, ModuleSurface] = {
    "billing": ModuleSurface(
        routes=("/api/v1/billing/*",),
        tables=("plan", "subscription", "webhook_event", "dunning_campaign", "usage_record"),
    ),
    "notifications": ModuleSurface(
        routes=("/api/v1/notifications/*", "/api/v1/me/notification-preferences"),
        tables=("notification", "push_token"),
    ),
    "file_storage": ModuleSurface(
        routes=("/api/v1/files/*",),
        tables=("stored_file",),
    ),
    "feature_flags": ModuleSurface(
        # No prefix on its router (app/modules/feature_flags/router.py), so
        # this one path is the whole module -- the /admin/orgs/{id}/feature-
        # flags routes belong to `admin`, which is why they are listed there.
        routes=("/api/v1/orgs/{org_id}/feature-flags",),
    ),
    "gdpr": ModuleSurface(
        # `/api/v1/me` exactly (account deletion), not `/api/v1/me/*`:
        # /me/support-context is always-on and /me/notification-preferences
        # belongs to notifications.
        routes=("/api/v1/me", "/api/v1/me/consents", "/api/v1/me/export/*"),
        tables=("consent_record", "data_export_job"),
    ),
    "admin": ModuleSurface(routes=("/api/v1/admin/*",)),
    "oauth": ModuleSurface(
        note=(
            "app/api/v1/__init__.py registers the OAuth router in the always-on "
            "block, so /api/v1/auth/oauth/* survives this toggle today -- it "
            "only changes what /api/v1/config reports and what the frontend "
            "renders. Moving it behind `if modules.oauth.enabled:` is what "
            "would give this entry routes."
        ),
    ),
    "totp_2fa": ModuleSurface(
        note=(
            "Same as oauth: /api/v1/auth/2fa/* is in build_router()'s always-on "
            "block, so the toggle is frontend-only today."
        ),
    ),
    "multi_org": ModuleSurface(
        note=(
            "UI-only by design. It owns no routes -- /api/v1/orgs is core "
            "tenancy -- and the frontend reads it from /config to hide the org "
            "switcher."
        ),
    ),
    "usage_metering": ModuleSurface(
        note=(
            "Phase-2 slot, nothing wired to it yet: /api/v1/billing/usage and "
            "the usage_record table both belong to `billing` (T-130's metering "
            "endpoints are what will move here)."
        ),
    ),
    "outbound_webhooks": ModuleSurface(
        # T-320/T-321/T-322. Like `public_api`, the toggle removes the whole
        # management surface -- but unlike it, there is nothing left behind:
        # a webhook has no second principal to authenticate, so with the
        # module off the only thing that survives is rows in two tables
        # (MODULE_TABLES again), and the delivery job refuses to send for
        # them (app/workers/jobs/webhooks.py checks `is_enabled` first).
        routes=("/api/v1/webhooks/*",),
        tables=("webhook_endpoint", "webhook_delivery"),
    ),
    "marketing": ModuleSurface(
        note=(
            "The one toggle that gates a *build* rather than a router: "
            "apps/marketing's build script exits 0 without producing a dist "
            "(T-300). Nothing in this process can observe that, which is "
            "precisely why CI's module-matrix job exists."
        ),
    ),
    "public_api": ModuleSurface(
        # The *management* surface, which is all the toggle removes. The two
        # routes an API key can call (T-311) belong to `tenancy` and stay
        # registered -- a key is a second way to authenticate against
        # endpoints the product already serves, not a parallel API. What the
        # toggle removes there is the key's ability to authenticate at all,
        # which app/core/principal.py checks on the auth path and
        # tests/modules/test_api_key_auth.py asserts; a routing assertion
        # could not see it.
        # T-314's published contract goes with the module too: with
        # `public_api` off a key cannot authenticate at all, and a contract
        # nobody can call is worse than no contract.
        routes=("/api/v1/api-keys/*", "/api/v1/openapi-public.json"),
        tables=("api_key",),
    ),
    "enterprise_sso": ModuleSurface(
        routes=("/api/v1/auth/sso/*",),
        tables=("sso_connection",),
    ),
}

_ALL_MODULES = get_args(ModuleName)

# Every module on. The baseline each case is diffed against, and legal in its
# own right (enterprise_sso's `requires=["oauth"]` is satisfied).
ALL_ENABLED = EnabledModules(**{name: ModuleConfig(enabled=True) for name in _ALL_MODULES})


class ModuleConfigCase(NamedTuple):
    name: str
    override: EnabledModules


MODULE_CONFIGS = [
    ModuleConfigCase(
        name="no_billing",
        override=EnabledModules(
            billing=ModuleConfig(enabled=False),
            notifications=ModuleConfig(enabled=False),  # requires billing (T-060)
            file_storage=ModuleConfig(enabled=False),  # requires billing (T-060)
        ),
    ),
    ModuleConfigCase(
        name="no_gdpr",
        override=EnabledModules(gdpr=ModuleConfig(enabled=False)),
    ),
    # T-291. Both ship off by default, so these two cases assert an absence
    # that is already true -- that is the point: they are the guard against
    # either module adding its router or table unconditionally. T-310 landed
    # `public_api`'s, so this case now asserts a real removal rather than a
    # vacuous one.
    ModuleConfigCase(
        name="no_public_api",
        override=EnabledModules(public_api=ModuleConfig(enabled=False)),
    ),
    ModuleConfigCase(
        name="no_enterprise_sso",
        override=EnabledModules(enterprise_sso=ModuleConfig(enabled=False)),
    ),
    ModuleConfigCase(
        # All fourteen off -- the in-process twin of CI's combination B, and
        # the configuration a template user is most likely to want. Legal
        # because every `requires` edge points at something that is also off.
        name="minimal",
        override=EnabledModules(
            **{name: ModuleConfig(enabled=False) for name in _ALL_MODULES},
        ),
    ),
]

# Sanity floor for the diff below: whatever else a case removes, the product
# still has to be a product. Checked explicitly (rather than trusted to the
# set difference) so a bug that emptied the route table entirely would fail
# with a readable message instead of an empty-set comparison.
_ALWAYS_ON_PATHS = ["/api/v1/auth/login", "/api/v1/orgs", "/api/v1/config"]


@pytest.fixture(autouse=True)
def _clear_modules_cache() -> Generator[None]:
    module_loader.get_modules.cache_clear()
    yield
    module_loader.get_modules.cache_clear()


@pytest.fixture
def postgres_container(fresh_database: object) -> object:
    """A per-test database on the shared server (see tests/conftest.py).

    These tests migrate an empty schema, so they need a clean database
    each -- which is what this is. It used to be a whole container each.
    """
    return fresh_database


@pytest.fixture
def real_config(postgres_container: DatabaseHandle, monkeypatch: pytest.MonkeyPatch) -> Config:
    url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    return migration_runner.build_config()


def _matches(surface: ModuleSurface, path: str) -> bool:
    for pattern in surface.routes:
        if pattern.endswith("/*"):
            stem = pattern[:-2]
            if path == stem or path.startswith(f"{stem}/"):
                return True
        elif path == pattern:
            return True
    return False


def _owns(module: ModuleName, path: str) -> bool:
    return _matches(MODULE_SURFACE[module], path)


def _paths(modules: EnabledModules, monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """Every route path build_router() produces under `modules`.

    iter_route_contexts, not app.openapi()["paths"]: the admin router is
    registered with include_in_schema=False, so it is invisible to the schema
    whether or not the module is enabled -- asserting its absence from the
    OpenAPI document would assert nothing at all.
    """
    monkeypatch.setattr(product_config, "MODULES", modules)
    module_loader.get_modules.cache_clear()
    app = FastAPI()
    app.include_router(build_router())
    return {ctx.path for ctx in iter_route_contexts(app.routes) if ctx.path.startswith("/api/")}


def _table_names(config: Config) -> set[str]:
    url = config.get_main_option("sqlalchemy.url") or str(get_settings().DATABASE_URL)

    async def _fetch() -> set[str]:
        conn = await asyncpg.connect(url.replace("+asyncpg", ""))
        try:
            rows = await conn.fetch("select tablename from pg_tables where schemaname = 'public'")
            return {row["tablename"] for row in rows}
        finally:
            await conn.close()

    return asyncio.run(_fetch())


def _disabled(modules: EnabledModules) -> list[ModuleName]:
    return [name for name in _ALL_MODULES if not getattr(modules, name).enabled]


# ── The matrix ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("case", MODULE_CONFIGS, ids=lambda c: c.name)
def test_module_config_matrix(
    case: ModuleConfigCase,
    real_config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disabled = _disabled(case.override)

    baseline = _paths(ALL_ENABLED, monkeypatch)
    expected = {path for path in baseline if not any(_owns(module, path) for module in disabled)}
    paths = _paths(case.override, monkeypatch)

    for always_on in _ALWAYS_ON_PATHS:
        assert always_on in paths, (
            f"{case.name}: {always_on} must survive every optional module toggle"
        )
    # Equality, not two subset checks: `paths - expected` is a route that
    # should have gone with its module and didn't, `expected - paths` is
    # collateral damage (or a surface entry in MODULE_SURFACE that has gone
    # stale). Both are the bug this file exists to catch.
    assert paths == expected, (
        f"{case.name}: routes still present that should be gone: "
        f"{sorted(paths - expected)}; routes missing that should have stayed: "
        f"{sorted(expected - paths)}"
    )

    migration_runner.upgrade(real_config)
    tables = _table_names(real_config)
    for module in disabled:
        for table in MODULE_SURFACE[module].tables:
            assert table not in tables, (
                f"{case.name}: table '{table}' should not exist with {module} disabled"
            )
    # The other half: an enabled module's tables are actually there, so a
    # migration that silently stopped running cannot pass as a toggle working.
    for module in _ALL_MODULES:
        if module in disabled or module in _SURFACE_PENDING:
            continue
        for table in MODULE_SURFACE[module].tables:
            assert table in tables, (
                f"{case.name}: table '{table}' is missing though {module} is enabled"
            )


def test_enterprise_sso_without_oauth_is_refused_at_boot() -> None:
    """The one Phase 4 combination that must never boot.

    T-290 gave `enterprise_sso` `requires=["oauth"]` because T-331's OIDC SP
    reuses authlib's client registry and T-025's account linking. The error
    message has to name both modules -- an operator reading a crashed boot log
    needs to know which switch to flip. tests/test_module_loader.py
    parametrises the same rule across every edge; this is the Phase 4 one, kept
    here so every toggle assertion is findable from one file.
    """
    illegal = replace(
        ALL_ENABLED,
        oauth=ModuleConfig(enabled=False),
        enterprise_sso=ModuleConfig(enabled=True),
    )

    with pytest.raises(RuntimeError, match=r"'enterprise_sso' requires 'oauth'"):
        module_loader.validate_modules(illegal)


# ── The matrix's own guards (no containers, no migrations) ──────────────────


def test_every_registered_module_has_a_declared_surface() -> None:
    """A fifteenth module cannot be added to ModuleName and forgotten here.

    Without this, a new toggle would silently assert nothing: every case's
    diff would agree, because nothing was ever claimed to be removed.
    """
    assert set(MODULE_SURFACE) == set(_ALL_MODULES)


def test_every_module_is_switched_off_by_at_least_one_case() -> None:
    covered = {module for case in MODULE_CONFIGS for module in _disabled(case.override)}

    assert covered == set(_ALL_MODULES)


def test_an_empty_surface_says_why() -> None:
    """Owning nothing removable is a legitimate state -- silence about it isn't.

    `multi_org` is UI-only, `usage_metering` is an unwired slot, `marketing`
    gates a build this process can't see. Each is a different reason, and the
    difference matters to whoever reads a passing test and wonders what it
    proved.
    """
    unexplained = [
        module
        for module, surface in MODULE_SURFACE.items()
        if not surface.routes and not surface.tables and not surface.note
    ]

    assert not unexplained, (
        f"modules with an empty surface and no note: {unexplained}. Say what the "
        f"toggle does today, or give it the routes/tables it actually removes."
    )


def test_every_declared_route_pattern_matches_a_real_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo'd pattern asserts nothing, quietly, forever."""
    baseline = _paths(ALL_ENABLED, monkeypatch)
    dead = []
    for module, surface in MODULE_SURFACE.items():
        if module in _SURFACE_PENDING:
            continue
        for pattern in surface.routes:
            single = ModuleSurface(routes=(pattern,))
            if not any(_matches(single, path) for path in baseline):
                dead.append(f"{module}: {pattern}")

    assert not dead, f"MODULE_SURFACE patterns matching no route: {dead}"


def test_a_pending_surface_is_retired_once_its_module_ships(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-310/T-331's reminder, delivered at the moment it becomes actionable.

    While `public_api`/`enterprise_sso` have no router, their entries in
    MODULE_SURFACE assert an absence that is trivially true. The day one of
    them serves a route, that entry starts doing real work and the module
    belongs out of _SURFACE_PENDING -- which also switches its tables back on
    in the "enabled modules have their tables" half of the matrix.
    """
    baseline = _paths(ALL_ENABLED, monkeypatch)
    shipped = [
        module for module in _SURFACE_PENDING if any(_owns(module, path) for path in baseline)
    ]

    assert not shipped, (
        f"{shipped} now serve routes: drop them from _SURFACE_PENDING so the "
        f"matrix asserts their tables too, and add their MODULE_BRANCHES entry "
        f"in app/core/migrations.py."
    )
