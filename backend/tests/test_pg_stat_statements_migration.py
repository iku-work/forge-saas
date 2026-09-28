"""T-205: the `pg_stat_statements` extension migration round-trips cleanly.

CREATE EXTENSION only registers catalog objects (views/functions) -- it
succeeds against a plain testcontainers Postgres even without
`shared_preload_libraries` set. Actually *collecting* stats additionally
requires the library to be preloaded at postmaster start (docker-compose.yml
/ docker-compose.prod.yml's postgres `command`), which this test's container
doesn't set -- querying `pg_stat_statements` for real rows is a documented
manual check against the dev stack (see docs/grafana/README.md), not
something a plain container can assert.
"""

import asyncio

import asyncpg
import pytest
from alembic import command
from alembic.config import Config

from app.core import migrations as migration_runner
from app.core.config import get_settings
from tests._database import DatabaseHandle


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


def _extension_installed(config: Config) -> bool:
    url = config.get_main_option("sqlalchemy.url") or str(get_settings().DATABASE_URL)

    async def _fetch() -> bool:
        conn = await asyncpg.connect(url.replace("+asyncpg", ""))
        try:
            row = await conn.fetchrow(
                "select 1 from pg_extension where extname = 'pg_stat_statements'"
            )
            return row is not None
        finally:
            await conn.close()

    return asyncio.run(_fetch())


def test_migration_creates_pg_stat_statements_extension(real_config: Config) -> None:
    migration_runner.upgrade(real_config)

    assert _extension_installed(real_config)


def test_round_trip_up_down_up_drops_and_recreates_extension(real_config: Config) -> None:
    migration_runner.upgrade(real_config)

    command.downgrade(real_config, "base")
    assert not _extension_installed(real_config)

    migration_runner.upgrade(real_config)
    assert _extension_installed(real_config)
