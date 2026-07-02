"""
T-066: module-toggle smoke test matrix.

test_api_router.py already proves a disabled module's routes 404, and
test_conditional_migrations.py already proves a disabled module's tables
never get created -- each in isolation. This file is the integration check
the spec asks for before Phase 2 adds more modules: that flipping one switch
in product.config.py removes a module's HTTP surface *and* its DB tables
together, the way a product author actually experiences the toggle.

agents.md's T-066 example matrix is written against modules this template
doesn't build until Phase 2 -- file_storage, feature_flags, admin, and
"outbound_webhooks" all still ship as empty `.gitkeep` directories.
notifications gained models.py + a migration in T-101 (Phase 2), and a real
router in T-103, so app/core/migrations.py's MODULE_BRANCHES now lists
"billing", "gdpr", and "notifications", and the no_billing/minimal scenarios
below assert its `/api/v1/notifications/*` + `/api/v1/me/notification-
preferences` routes absent too, cascading off `requires=["billing"]` (T-060)
the same way its tables already did. The matrix below substitutes
"no_billing" / "no_gdpr" / "minimal" for the spec's "no_billing" / "no_admin"
/ "minimal" -- same shape (disable one module, disable a dependent pair,
disable everything optional), same guarantee, adapted to the modules that
actually exist. file_storage is still only exercised via the
`requires=["billing"]` cascade (no router of its own yet), not as a
standalone scenario.

Each scenario gets its own Postgres container (mirrors
test_conditional_migrations.py's `real_config` fixture) since each drives a
fresh `alembic upgrade` under a distinct module configuration that must not
leak into the session-scoped `engine` fixture other test files share.
"""

import asyncio
from collections.abc import Generator
from typing import NamedTuple

import asyncpg
import pytest
from alembic.config import Config
from fastapi import FastAPI
from testcontainers.postgres import PostgresContainer

from app.api.v1 import build_router
from app.core import migrations as migration_runner
from app.core import module_loader
from app.core.config import get_settings
from app.core.modules import EnabledModules, ModuleConfig
from product import config as product_config


class ModuleConfigCase(NamedTuple):
    name: str
    override: EnabledModules
    absent_routes: list[str]
    absent_tables: list[str]


MODULE_CONFIGS = [
    ModuleConfigCase(
        name="no_billing",
        override=EnabledModules(
            billing=ModuleConfig(enabled=False),
            notifications=ModuleConfig(enabled=False),  # requires billing (T-060)
            file_storage=ModuleConfig(enabled=False),  # requires billing (T-060)
        ),
        absent_routes=[
            "/api/v1/billing/plans",
            "/api/v1/billing/checkout-session",
            "/api/v1/billing/portal-session",
            "/api/v1/billing/subscription",
            "/api/v1/billing/webhooks/stripe",
            "/api/v1/notifications",
            "/api/v1/notifications/stream",
            "/api/v1/me/notification-preferences",
        ],
        absent_tables=["plan", "subscription", "webhook_event", "notification", "push_token"],
    ),
    ModuleConfigCase(
        name="no_gdpr",
        override=EnabledModules(gdpr=ModuleConfig(enabled=False)),
        absent_routes=["/api/v1/me/export", "/api/v1/me/consents"],
        absent_tables=["consent_record", "data_export_job"],
    ),
    ModuleConfigCase(
        name="minimal",  # auth + tenancy + audit + config only
        override=EnabledModules(
            billing=ModuleConfig(enabled=False),
            notifications=ModuleConfig(enabled=False),
            file_storage=ModuleConfig(enabled=False),
            gdpr=ModuleConfig(enabled=False),
        ),
        absent_routes=[
            "/api/v1/billing/plans",
            "/api/v1/me/export",
            "/api/v1/notifications",
            "/api/v1/me/notification-preferences",
        ],
        absent_tables=[
            "plan",
            "subscription",
            "webhook_event",
            "consent_record",
            "data_export_job",
            "notification",
            "push_token",
        ],
    ),
]

_ALWAYS_ON_PATHS = ["/api/v1/auth/login", "/api/v1/orgs", "/api/v1/config"]


@pytest.fixture(autouse=True)
def _clear_modules_cache() -> Generator[None]:
    module_loader.get_modules.cache_clear()
    yield
    module_loader.get_modules.cache_clear()


@pytest.fixture
def postgres_container() -> Generator[PostgresContainer]:
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture
def real_config(postgres_container: PostgresContainer, monkeypatch: pytest.MonkeyPatch) -> Config:
    url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    return migration_runner.build_config()


def _paths() -> set[str]:
    app = FastAPI()
    app.include_router(build_router())
    return set(app.openapi()["paths"])


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


@pytest.mark.parametrize("case", MODULE_CONFIGS, ids=lambda c: c.name)
def test_module_config_matrix(
    case: ModuleConfigCase,
    real_config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(product_config, "MODULES", case.override)
    module_loader.get_modules.cache_clear()

    paths = _paths()
    for route in case.absent_routes:
        assert route not in paths, f"{case.name}: {route} should be absent from the route table"
    for always_on in _ALWAYS_ON_PATHS:
        assert always_on in paths, (
            f"{case.name}: {always_on} must survive every optional module toggle"
        )

    migration_runner.upgrade(real_config)
    tables = _table_names(real_config)
    for table in case.absent_tables:
        assert table not in tables, f"{case.name}: table '{table}' should not exist"
