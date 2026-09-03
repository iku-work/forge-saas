"""T-244: the app actually works through a transaction-mode pgbouncer.

Everything in the first half of this module runs against a *real* pgbouncer
container in front of a real Postgres, on a shared docker network -- the whole
point of the task is a driver-level interaction (asyncpg's server-side
prepared statements vs. a server connection that gets re-assigned at every
transaction boundary) that no amount of mocking can reproduce. The pgbouncer
container is configured from the committed `.env.pgbouncer.example`, so this
suite fails if that file drifts away from a working configuration.

See docs/adr/0002-pgbouncer.md for the reasoning and the measurements.
"""

import asyncio
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
import yaml
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from testcontainers.core.container import DockerContainer
from testcontainers.core.network import Network
from testcontainers.core.waiting_utils import wait_for_logs
from testcontainers.postgres import PostgresContainer

from app.core.config import Settings, get_settings
from app.core.db import build_connect_args
from app.core.migrations import build_config
from app.core.migrations import upgrade as upgrade_migrations

REPO_ROOT = Path(__file__).parent.parent.parent
PGBOUNCER_ENV_EXAMPLE = REPO_ROOT / ".env.pgbouncer.example"
PROD_COMPOSE = REPO_ROOT / "docker-compose.prod.yml"

# Network alias the pgbouncer container reaches Postgres by. Any name works;
# it just has to match the DATABASE_URL handed to pgbouncer below.
_PG_ALIAS = "t244-postgres"

# The two settings overridden from .env.pgbouncer.example when building the
# test's pgbouncer. Production sizes the pool so client connections rarely
# have to share a server connection; these tests need the opposite -- one
# server connection, shared by everything -- because that is what actually
# exercises "your prepared statement is on a connection you no longer hold".
_TEST_POOL_OVERRIDES = {"DEFAULT_POOL_SIZE": "1", "MAX_DB_CONNECTIONS": "1"}


@pytest.fixture(scope="module")
def pgbouncer_url(pgbouncer_stack: tuple[str, str]) -> str:
    """asyncpg DSN pointing at pgbouncer."""
    return pgbouncer_stack[0]


@pytest.fixture(scope="module")
def direct_url(pgbouncer_stack: tuple[str, str]) -> str:
    """asyncpg DSN pointing straight at Postgres, bypassing pgbouncer."""
    return pgbouncer_stack[1]


@pytest.fixture(scope="module")
def pgbouncer_stack() -> Generator[tuple[str, str], None, None]:
    """(pooled DSN, direct DSN) for a live pgbouncer -> Postgres pair."""
    env = {k: v for k, v in dotenv_values(PGBOUNCER_ENV_EXAMPLE).items() if v is not None}
    with Network() as network:
        postgres = PostgresContainer("postgres:16-alpine")
        postgres.with_network(network).with_network_aliases(_PG_ALIAS)
        with postgres:
            user = postgres.username
            password = postgres.password
            dbname = postgres.dbname
            env |= {
                # The one value that can't come from the example file: real
                # credentials for this throwaway container. The image's
                # entrypoint parses it into both the [databases] entry and the
                # auth_file userlist, exactly as it does in production.
                "DATABASE_URL": f"postgres://{user}:{password}@{_PG_ALIAS}:5432/{dbname}",
                "DB_USER": user,
                "DB_NAME": dbname,
                "PGPASSWORD": password,
                **_TEST_POOL_OVERRIDES,
            }
            pgbouncer = DockerContainer(_pinned_pgbouncer_image())
            pgbouncer.with_network(network).with_exposed_ports(6432)
            for key, value in env.items():
                pgbouncer.with_env(key, value)
            with pgbouncer:
                wait_for_logs(pgbouncer, "process up", timeout=60)
                host = pgbouncer.get_container_host_ip()
                port = pgbouncer.get_exposed_port(6432)
                pooled = f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{dbname}"
                direct = postgres.get_connection_url().replace("psycopg2", "asyncpg")
                yield pooled, direct


def _pinned_pgbouncer_image() -> str:
    """The image docker-compose.prod.yml actually deploys.

    Read from the compose file rather than duplicated here, so a version bump
    is tested by the same commit that makes it.
    """
    compose = yaml.safe_load(PROD_COMPOSE.read_text())
    return str(compose["services"]["pgbouncer"]["image"])


def _pgbouncer_settings(**overrides: Any) -> Settings:  # noqa: ANN401 — mirrors Settings' own fields
    return get_settings().model_copy(update={"PGBOUNCER_ENABLED": True, **overrides})


def _engine(url: str, *, connect_args: dict[str, Any]) -> AsyncEngine:
    # pool_size=1 so a single connection is reused across transactions, which
    # is what makes the failure below deterministic rather than a race.
    return create_async_engine(url, pool_size=1, max_overflow=0, connect_args=connect_args)


async def _two_sequential_transactions(engine: AsyncEngine) -> None:
    """One connection, the same statement, in two separate transactions.

    Between the commit and the second execute, pgbouncer releases the server
    connection back to its pool -- there is no guarantee the second
    transaction lands on the same Postgres backend, and DISCARD ALL has wiped
    whatever the first one prepared.
    """
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1 AS repeated"))
        await conn.commit()
        await conn.execute(text("SELECT 1 AS repeated"))
        await conn.commit()


# --------------------------------------------------------------------------
# The tradeoff, demonstrated both ways
# --------------------------------------------------------------------------


async def test_default_engine_breaks_through_transaction_pooling(pgbouncer_url: str) -> None:
    """The problem is real, not theoretical -- this is what T-244 exists for.

    Without build_connect_args, SQLAlchemy caches the asyncpg PreparedStatement
    per client connection and re-uses it in the next transaction, by which
    point it no longer exists server-side.
    """
    engine = _engine(pgbouncer_url, connect_args={})
    try:
        with pytest.raises(DBAPIError) as exc_info:
            await _two_sequential_transactions(engine)
    finally:
        await engine.dispose()

    message = str(exc_info.value)
    assert "prepared statement" in message
    assert "does not exist" in message


async def test_configured_engine_survives_transaction_pooling(pgbouncer_url: str) -> None:
    engine = _engine(pgbouncer_url, connect_args=build_connect_args(_pgbouncer_settings()))
    try:
        await _two_sequential_transactions(engine)
    finally:
        await engine.dispose()


async def test_repeated_identical_queries_do_not_collide(pgbouncer_url: str) -> None:
    """No DuplicatePreparedStatementError across multiplexed client connections.

    Ten client connections share the single server connection this fixture's
    pgbouncer is limited to, each running the identical statement many times.
    With asyncpg's default per-connection statement naming they would all
    claim `__asyncpg_stmt_1__` on that one backend.
    """
    engine = create_async_engine(
        pgbouncer_url,
        pool_size=10,
        max_overflow=0,
        connect_args=build_connect_args(_pgbouncer_settings()),
    )

    async def hammer() -> None:
        for _ in range(10):
            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT 42 AS answer"))
                assert result.scalar() == 42
                await conn.commit()

    try:
        await asyncio.gather(*(hammer() for _ in range(10)))
    finally:
        await engine.dispose()


async def test_connect_args_are_only_paid_for_when_pgbouncer_is_enabled() -> None:
    assert build_connect_args(get_settings().model_copy(update={"PGBOUNCER_ENABLED": False})) == {}

    args = build_connect_args(_pgbouncer_settings())
    assert args["statement_cache_size"] == 0
    assert args["prepared_statement_cache_size"] == 0
    # Unique per call -- a shared name is exactly the collision this prevents.
    name_func = args["prepared_statement_name_func"]
    assert name_func() != name_func()


async def test_transaction_rollback_still_isolates_through_pgbouncer(pgbouncer_url: str) -> None:
    """Transaction semantics are unchanged by the pooler.

    A pooler that leaked a transaction across the connection boundary would
    show up here as a surviving row.
    """
    engine = _engine(pgbouncer_url, connect_args=build_connect_args(_pgbouncer_settings()))
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE t244_rollback (id int primary key)"))

        async with engine.connect() as conn:
            await conn.execute(text("INSERT INTO t244_rollback VALUES (1)"))
            await conn.rollback()
            count = (await conn.execute(text("SELECT count(*) FROM t244_rollback"))).scalar()
            await conn.commit()
        assert count == 0
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS t244_rollback"))
        await engine.dispose()


# --------------------------------------------------------------------------
# Migrations go direct, and can run while pooled traffic flows
# --------------------------------------------------------------------------


async def _create_database(direct_url: str, name: str) -> str:
    engine = create_async_engine(direct_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        await engine.dispose()
    return direct_url.rsplit("/", 1)[0] + f"/{name}"


def _apply_settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    # alembic/env.py resolves the DSN through get_settings() at the moment
    # `alembic upgrade` runs, so the cache has to be dropped after the
    # env vars change, not before.
    get_settings.cache_clear()


async def test_alembic_connects_via_migrations_dsn_not_database_url(
    direct_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DATABASE_URL is deliberately unreachable here.

    If alembic/env.py still sourced its URL from DATABASE_URL, `upgrade`
    could not possibly succeed -- which is the whole assertion.
    """
    migrations_url = await _create_database(direct_url, "t244_migrations_only")
    _apply_settings(
        monkeypatch,
        DATABASE_URL="postgresql+asyncpg://nobody:nobody@127.0.0.1:1/unreachable",
        MIGRATIONS_DATABASE_URL=migrations_url,
        PGBOUNCER_ENABLED="true",
    )

    await asyncio.to_thread(upgrade_migrations, build_config())

    engine = create_async_engine(migrations_url)
    try:
        async with engine.connect() as conn:
            applied = (await conn.execute(text("SELECT count(*) FROM alembic_version"))).scalar()
    finally:
        await engine.dispose()
    assert applied is not None
    assert applied >= 1


async def test_migrations_run_while_traffic_flows_through_pgbouncer(
    direct_url: str, pgbouncer_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The production upgrade path: app serving through the pooler, alembic
    migrating over a direct connection, at the same time."""
    migrations_url = await _create_database(direct_url, "t244_migrations_live")
    _apply_settings(
        monkeypatch,
        DATABASE_URL=pgbouncer_url,
        MIGRATIONS_DATABASE_URL=migrations_url,
        PGBOUNCER_ENABLED="true",
    )

    engine = _engine(pgbouncer_url, connect_args=build_connect_args(_pgbouncer_settings()))
    stop = asyncio.Event()
    served = 0

    async def serve_requests() -> None:
        nonlocal served
        while not stop.is_set():
            await _two_sequential_transactions(engine)
            served += 1
            await asyncio.sleep(0)

    traffic = asyncio.create_task(serve_requests())
    try:
        await asyncio.to_thread(upgrade_migrations, build_config())
    finally:
        stop.set()
        await traffic
        await engine.dispose()

    assert served > 0, "the pooled traffic task never completed a request"


# --------------------------------------------------------------------------
# Startup invariants -- getting this wrong must fail loudly, not subtly
# --------------------------------------------------------------------------


FIXTURE_ENV_FILE = REPO_ROOT / "backend" / ".env.test"

# Same shape as test_config.py's _PROD_SAFE_OVERRIDES: everything a non-dev
# ENVIRONMENT demands, so a test can assert on one invariant without tripping
# the four unrelated ones first.
_PROD_SAFE_OVERRIDES: dict[str, object] = {
    "ENVIRONMENT": "prod",
    "FORCE_HTTPS": True,
    "VIRUS_SCANNER": "clamav",
    "PASSWORD_PEPPER": "a-real-pepper",
    "ALLOWED_ORIGINS": ["https://app.example.com"],
    "DATABASE_URL": "postgresql+asyncpg://u:p@pgbouncer:6432/forge?sslmode=require",
    "REDIS_URL": "rediss://redis:6379/0",
    "METRICS_BLOCKED_AT_EDGE": True,
}


def _clear_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # conftest's autouse _settings_env fixture puts every .env.test key on the
    # environment, and env vars outrank explicit kwargs in pydantic-settings.
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)


def test_pgbouncer_without_a_migrations_dsn_refuses_to_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_settings_env(monkeypatch)

    with pytest.raises(RuntimeError, match="MIGRATIONS_DATABASE_URL must be set"):
        Settings(_env_file=FIXTURE_ENV_FILE, PGBOUNCER_ENABLED=True)


def test_migrations_dsn_pointing_back_at_the_pooler_refuses_to_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_settings_env(monkeypatch)
    pooled = "postgresql+asyncpg://postgres:postgres@pgbouncer:6432/forge"

    with pytest.raises(RuntimeError, match="identical to DATABASE_URL"):
        Settings(
            _env_file=FIXTURE_ENV_FILE,
            PGBOUNCER_ENABLED=True,
            DATABASE_URL=pooled,
            MIGRATIONS_DATABASE_URL=pooled,
        )


def test_blank_migrations_dsn_means_unset_not_a_parse_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `MIGRATIONS_DATABASE_URL=` is how .env.example spells "not set" -- and
    # PostgresDsn, unlike the str-typed optional settings, rejects "".
    _clear_settings_env(monkeypatch)

    settings = Settings(_env_file=FIXTURE_ENV_FILE, MIGRATIONS_DATABASE_URL="")

    assert settings.MIGRATIONS_DATABASE_URL is None
    assert settings.migrations_database_url == str(settings.DATABASE_URL)


def test_migrations_dsn_must_use_tls_outside_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_settings_env(monkeypatch)

    with pytest.raises(RuntimeError, match="MIGRATIONS_DATABASE_URL must set sslmode"):
        Settings(
            _env_file=FIXTURE_ENV_FILE,
            **_PROD_SAFE_OVERRIDES,
            PGBOUNCER_ENABLED=True,
            # The direct hop to Postgres -- TLS on DATABASE_URL says nothing
            # about it, since it never traverses the pooler.
            MIGRATIONS_DATABASE_URL="postgresql+asyncpg://u:p@postgres:5432/forge",
        )


def test_tls_on_both_dsns_is_accepted_outside_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_settings_env(monkeypatch)

    settings = Settings(
        _env_file=FIXTURE_ENV_FILE,
        **_PROD_SAFE_OVERRIDES,
        PGBOUNCER_ENABLED=True,
        MIGRATIONS_DATABASE_URL="postgresql+asyncpg://u:p@postgres:5432/forge?sslmode=require",
    )

    assert "postgres:5432" in settings.migrations_database_url


# --------------------------------------------------------------------------
# The deployed configuration itself
# --------------------------------------------------------------------------


def _prod_compose() -> dict:
    return yaml.safe_load(PROD_COMPOSE.read_text())


def _pgbouncer_example_env() -> dict[str, str | None]:
    return dotenv_values(PGBOUNCER_ENV_EXAMPLE)


def test_pgbouncer_image_is_pinned() -> None:
    image = _pinned_pgbouncer_image()
    assert ":" in image, "pgbouncer image must be pinned to a tag"
    assert not image.endswith(":latest")


def test_pgbouncer_runs_in_transaction_mode() -> None:
    # Session mode would leave the connection count 1:1 with clients, i.e.
    # the entire point of T-244 silently undone by a one-word config change.
    assert _pgbouncer_example_env()["POOL_MODE"] == "transaction"


def test_pgbouncer_resets_server_connections_on_release() -> None:
    # Without both of these, the uniquely-named prepared statements
    # build_connect_args produces accumulate on every server connection until
    # server_lifetime recycles it -- see docs/adr/0002-pgbouncer.md.
    env = _pgbouncer_example_env()
    assert env["SERVER_RESET_QUERY"] == "DISCARD ALL"
    assert env["SERVER_RESET_QUERY_ALWAYS"] == "1"


def test_pgbouncer_uses_scram_auth() -> None:
    # Postgres 14+ defaults password_encryption to scram-sha-256; with
    # AUTH_TYPE=md5 the image's entrypoint stores an md5 digest in the
    # userlist and every server-side login fails.
    assert _pgbouncer_example_env()["AUTH_TYPE"] == "scram-sha-256"


def test_pgbouncer_pool_is_sized_under_postgres_max_connections() -> None:
    # T-244's brief: max_connections * 0.8, minus a superuser reserve.
    # Postgres' defaults are max_connections=100, superuser_reserved=3.
    env = _pgbouncer_example_env()
    server_connections = int(env["DEFAULT_POOL_SIZE"] or 0) + int(env["RESERVE_POOL_SIZE"] or 0)
    assert server_connections <= 80
    assert int(env["MAX_DB_CONNECTIONS"] or 0) <= server_connections


def test_backend_and_worker_wait_for_a_healthy_pgbouncer() -> None:
    services = _prod_compose()["services"]
    assert "healthcheck" in services["pgbouncer"]
    for name in ("backend", "worker"):
        depends = services[name]["depends_on"]
        assert depends["pgbouncer"]["condition"] == "service_healthy", name


def test_pgbouncer_healthcheck_proves_postgres_is_reachable() -> None:
    # A TCP/`SHOW POOLS` probe would report healthy while Postgres is down,
    # and `depends_on: service_healthy` would then start the backend against
    # a pooler that can serve nothing.
    check = " ".join(_prod_compose()["services"]["pgbouncer"]["healthcheck"]["test"])
    assert "psql" in check
    assert "6432" in check
    assert "SELECT 1" in check


def _pgbouncer_ci_job() -> dict:
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    return workflow["jobs"]["pgbouncer-prod-compose"]


def test_ci_boots_the_real_prod_compose_stack() -> None:
    # Nothing else in CI ever starts docker-compose.prod.yml -- the zap and
    # e2e jobs both use the dev stack, which has no pooler in the path.
    steps = _pgbouncer_ci_job()["steps"]
    boot = next(s for s in steps if "Boot postgres" in s.get("name", ""))
    assert "docker-compose.prod.yml" in boot["run"]
    assert "pgbouncer" in boot["run"]


def test_ci_migrates_after_the_app_is_already_serving_pooled_traffic() -> None:
    # Ordering is the assertion: migrating *while* pooled traffic flows is the
    # production upgrade path, and it only works because alembic uses
    # MIGRATIONS_DATABASE_URL rather than DATABASE_URL.
    step_names = [s.get("name", "") for s in _pgbouncer_ci_job()["steps"]]
    ready_index = next(i for i, n in enumerate(step_names) if "Wait for the backend" in n)
    migrate_index = next(i for i, n in enumerate(step_names) if "Migrate over the direct DSN" in n)
    assert ready_index < migrate_index


def test_ci_env_file_routes_the_app_through_pgbouncer_and_alembic_around_it() -> None:
    env = dotenv_values(REPO_ROOT / "backend" / ".env.prod-compose")
    assert env["PGBOUNCER_ENABLED"] == "true"
    assert "pgbouncer:6432" in (env["DATABASE_URL"] or "")
    assert "postgres:5432" in (env["MIGRATIONS_DATABASE_URL"] or "")


def test_dev_compose_stays_direct_to_postgres() -> None:
    # T-244 explicitly keeps the local stack pooler-free -- one less moving
    # part, and PGBOUNCER_ENABLED defaults to false to match.
    dev = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
    assert "pgbouncer" not in dev["services"]
    assert Settings.model_fields["PGBOUNCER_ENABLED"].default is False
