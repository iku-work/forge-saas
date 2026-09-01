"""T-062: conditional Alembic migrations.

Covers two distinct mechanisms:
  - app.core.migrations.migration_targets() / upgrade() -- which `alembic
    upgrade` targets get requested for the currently active
    product.config.py modules. This is what makes a disabled module's
    tables never exist, and what makes enabling a module later create its
    tables from scratch (re-running upgrade() asks Alembic for a branch
    head it has never seen before).
  - alembic/env.py's include_object hook -- what autogenerate is allowed to
    propose. This matters when a module's tables are already physically
    present in the DB (created while the module was enabled) and the module
    is later disabled: without include_object, the next `alembic revision
    --autogenerate` would propose dropping them.

Each test gets its own Postgres container (shadowing conftest.py's
session-scoped one) since these tests intentionally drive the schema
through several upgrade/downgrade transitions that must not leak into the
shared `engine` fixture other test files rely on.

Migration commands run their async engine via `asyncio.run()` inside
alembic/env.py, so every test function here is a plain sync `def` -- calling
into them from an `async def` test would collide with pytest-asyncio's
already-running event loop (the same reason conftest.py's own `engine`
fixture is a sync fixture despite running migrations against a real DB).
"""

import asyncio
import re
import shutil
import subprocess
import sys
from collections.abc import Generator
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from sqlmodel import SQLModel
from testcontainers.postgres import PostgresContainer

from app.core import migrations as migration_runner
from app.core import module_loader
from app.core.config import get_settings
from app.core.modules import EnabledModules, ModuleConfig

# Imported at module scope on purpose (T-250): it registers the _template
# scaffold's table on the shared SQLModel.metadata for every autogenerate
# test in this file, not just the one that asserts on it -- which is the
# state a full-suite run is actually in, since anything that sweeps
# app.modules.* imports the scaffold too.
from app.modules._template.models import Item as TemplateItem
from product import config as product_config

_REAL_ALEMBIC_DIR = Path(__file__).parent.parent / "alembic"

_DISABLE_BILLING = EnabledModules(
    billing=ModuleConfig(enabled=False),
    notifications=ModuleConfig(enabled=False),  # requires billing
    file_storage=ModuleConfig(enabled=False),  # requires billing
)


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


def test_migration_targets_always_includes_core_head() -> None:
    assert migration_runner.CORE_HEAD_REVISION in migration_runner.migration_targets()


def test_migration_targets_includes_only_enabled_optional_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(product_config, "MODULES", _DISABLE_BILLING)

    targets = migration_runner.migration_targets()

    assert "billing@head" not in targets
    assert "gdpr@head" in targets


# The five tests marked below read the *committed* product/config.py rather
# than supplying their own override, which is exactly what CI's module-matrix
# job replaces. Everything else in this file monkeypatches MODULES itself and
# is therefore independent of what the product actually ships.
@pytest.mark.module_dependent
def test_migration_targets_includes_all_enabled_branches_by_default() -> None:
    targets = migration_runner.migration_targets()

    assert "billing@head" in targets
    assert "gdpr@head" in targets


def test_plan_summary_reports_enabled_and_disabled_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(product_config, "MODULES", _DISABLE_BILLING)

    summary = migration_runner.plan_summary()

    assert "core" in summary
    assert "billing" in summary
    assert "disabled" in summary
    assert "gdpr" in summary
    assert "enabled" in summary


def test_main_plan_argument_prints_summary_without_touching_the_db(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["migrations.py", "plan"])

    migration_runner.main()

    assert "core" in capsys.readouterr().out


def test_main_with_no_argument_runs_upgrade(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Config] = []
    monkeypatch.setattr("sys.argv", ["migrations.py"])
    monkeypatch.setattr(migration_runner, "upgrade", calls.append)

    migration_runner.main()

    assert len(calls) == 1


def test_billing_disabled_creates_no_billing_tables(
    real_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(product_config, "MODULES", _DISABLE_BILLING)

    migration_runner.upgrade(real_config)

    tables = _table_names(real_config)
    assert {"plan", "subscription", "webhook_event"}.isdisjoint(tables)
    assert "consent_record" in tables  # gdpr stays enabled -- unaffected by billing toggle


@pytest.mark.module_dependent
def test_billing_enabled_creates_billing_tables(real_config: Config) -> None:
    migration_runner.upgrade(real_config)  # default product config: billing enabled

    tables = _table_names(real_config)
    assert {"plan", "subscription", "webhook_event"} <= tables


@pytest.mark.module_dependent
def test_the_migrations_build_exactly_what_the_models_declare(real_config: Config) -> None:
    """No drift between `alembic upgrade` and the models.

    `alembic check` is the same comparison `revision --autogenerate` makes,
    with no script written. A migration that creates something no model
    declares -- T-132's `usage_record` did both: a `UniqueConstraint` beside
    an already-unique column, and a composite index missing from
    `__table_args__` -- leaves a diff that every later autogenerate run
    proposes to "fix" by dropping. That is how a deliberate index gets deleted
    by someone applying a generated script nobody read, and the noise hides
    the one diff that mattered.

    Default product config, so this covers every enabled module's branch at
    once.

    Run as a subprocess rather than via `command.check` in-process, because
    `SQLModel.metadata` is global and the test suite adds to it: TestEntity
    (test_base_repository), ReplicaEntity (test_read_replica) and
    AlembicRoundTripEntity (test_alembic) are all registered on it once pytest
    has collected those files, and no migration creates them. In-process this
    test therefore passed alone and failed in a full run -- which is also
    exactly what a developer gets, since a fresh `alembic` CLI imports only
    app models. Checking the way the CLI does keeps the answer honest.

    **Every branch is applied first, including the disabled modules'** -- and
    that is a statement about alembic rather than about this product. `alembic
    check` refuses to compare anything at all while a *script* head is missing
    from `alembic_version` ("Target database is not up to date"), and a module
    that ships off (T-310's `public_api`, T-331's `enterprise_sso`) has a head
    that `migration_runner.upgrade()` deliberately never applies. Without this
    loop the check does not report drift, it reports that it declined to look.

    The extra tables that leaves in the database are not drift either:
    `alembic/env.py`'s `include_object` excludes a disabled module's tables
    from the comparison, which is exactly the situation it was written for --
    a table physically present from a time the module was enabled. So this
    test asserts the same property it always did for every enabled module, and
    now also proves that a disabled module's branch applies cleanly on top of
    a fully-migrated database.

    Note for anyone running `alembic check` by hand on a product with a module
    turned off: they will hit the same refusal, and the fix is the same one --
    apply the disabled branch first. TEMPLATE.md's migration section says so.
    """
    migration_runner.upgrade(real_config)
    # The branches migration_targets() skipped, so `alembic check` has
    # something it is willing to compare. See the docstring.
    for branch in migration_runner.MODULE_BRANCHES.values():
        command.upgrade(real_config, f"{branch}@head")

    # DATABASE_URL is already pointed at this test's container by real_config's
    # monkeypatch.setenv, so the child inherits it.
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        cwd=_REAL_ALEMBIC_DIR.parent,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, (
        "alembic check reported drift between the migrations and the models:\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_deleting_an_organization_takes_its_credentials_with_it(real_config: Config) -> None:
    """T-310's rule, checked against the schema `alembic upgrade` actually
    builds, for every table that stores a credential.

    "A live credential outliving its tenant is the worst orphan this schema
    could produce" is `ApiKey.org_id`'s own comment, and three tables now hold
    one: `api_key` (a digest), `webhook_endpoint` (an HMAC signing secret) and
    `sso_connection` (an IdP client secret). All three ship in modules that
    are **off** by default, so this runs after every branch is applied.

    Behavioural rather than a read of `pg_constraint`, because what has to be
    true is that the delete *happens*, and because `alembic check` cannot see
    this class of drift at all: autogenerate compares a foreign key's columns
    and target, not its ON DELETE rule, so a model saying CASCADE over a
    migration that forgot it is invisible to the drift test above. That is
    exactly what had happened to both webhook tables (T-350).

    There is no org-delete endpoint -- the only thing that can remove that row
    is an operator at a psql prompt -- which is the reason to enforce it in
    the database rather than in a service that such a person never runs.
    """
    migration_runner.upgrade(real_config)
    for branch in migration_runner.MODULE_BRANCHES.values():
        command.upgrade(real_config, f"{branch}@head")

    url = (real_config.get_main_option("sqlalchemy.url") or "").replace("+asyncpg", "")

    async def _run() -> dict[str, int]:
        conn = await asyncpg.connect(url)
        try:
            org_id = await conn.fetchval(
                "insert into organization (id, name, slug, settings, created_at, "
                "updated_at) values (gen_random_uuid(), 'Doomed', 'doomed', "
                "'{}'::jsonb, now(), now()) returning id"
            )
            await conn.execute(
                "insert into api_key (id, org_id, name, prefix, key_hash, scopes, "
                "created_at, updated_at) values (gen_random_uuid(), $1, 'k', 'pfx', "
                "'digest', '[]'::jsonb, now(), now())",
                org_id,
            )
            endpoint_id = await conn.fetchval(
                "insert into webhook_endpoint (id, org_id, url, secret, event_types, "
                "enabled, consecutive_failures, created_at, updated_at) values "
                "(gen_random_uuid(), $1, 'https://x.example/h', 'enc', '[]'::jsonb, "
                "true, 0, now(), now()) returning id",
                org_id,
            )
            await conn.execute(
                "insert into webhook_delivery (id, org_id, endpoint_id, event_type, "
                "payload, status, attempt_count, delivery_id, created_at, updated_at) "
                "values (gen_random_uuid(), $1, $2, 'x.y', '{}'::jsonb, 'pending', 0, "
                "gen_random_uuid(), now(), now())",
                org_id,
                endpoint_id,
            )
            await conn.execute(
                "insert into sso_connection (id, org_id, provider_label, issuer, "
                "discovery_url, client_id, client_secret, email_domain, jit_provisioning, "
                "default_role, group_role_mappings, enforced, domain_hint_enabled, enabled, "
                "created_at, updated_at) values (gen_random_uuid(), $1, 'IdP', "
                "'https://idp.example', 'https://idp.example/.well-known/openid-configuration', "
                "'cid', 'enc', 'example.com', true, 'MEMBER', '{}'::jsonb, false, false, true, "
                "now(), now())",
                org_id,
            )

            # What an operator does. If any of these foreign keys lacks its ON
            # DELETE rule this raises ForeignKeyViolationError rather than
            # leaving an orphan -- safe, but it means "remove this customer"
            # is a puzzle instead of a DELETE, which is the bug this catches.
            await conn.execute("delete from organization where id = $1", org_id)

            return {
                table: await conn.fetchval(
                    f"select count(*) from {table} where org_id = $1",  # noqa: S608
                    org_id,
                )
                for table in (
                    "api_key",
                    "webhook_endpoint",
                    "webhook_delivery",
                    "sso_connection",
                )
            }
        finally:
            await conn.close()

    assert asyncio.run(_run()) == {
        "api_key": 0,
        "webhook_endpoint": 0,
        "webhook_delivery": 0,
        "sso_connection": 0,
    }


def test_round_trip_up_down_up_with_billing_disabled(
    real_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(product_config, "MODULES", _DISABLE_BILLING)

    migration_runner.upgrade(real_config)
    command.downgrade(real_config, "base")
    migration_runner.upgrade(real_config)

    tables = _table_names(real_config)
    assert {"plan", "subscription", "webhook_event"}.isdisjoint(tables)
    assert "consent_record" in tables


def test_enabling_billing_later_creates_tables_from_scratch(
    real_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(product_config, "MODULES", _DISABLE_BILLING)
    migration_runner.upgrade(real_config)
    tables_before = _table_names(real_config)
    assert {"plan", "subscription", "webhook_event"}.isdisjoint(tables_before)

    module_loader.get_modules.cache_clear()
    monkeypatch.setattr(product_config, "MODULES", EnabledModules())  # billing back on
    migration_runner.upgrade(real_config)

    tables_after = _table_names(real_config)
    assert {"plan", "subscription", "webhook_event"} <= tables_after


# --- autogenerate / include_object -----------------------------------------
#
# A single flat (unbranched) versions/ directory, separate from the real
# backend/alembic/versions/ tree -- branch labels are irrelevant to what
# include_object excludes, and a flat chain sidesteps Alembic's "multiple
# heads"/"target database is not up to date" guards that the real
# multi-branch tree would otherwise trip on a from-scratch autogenerate.


@pytest.fixture
def blank_alembic_config(
    tmp_path: Path, postgres_container: PostgresContainer, monkeypatch: pytest.MonkeyPatch
) -> Config:
    alembic_dir = tmp_path / "alembic"
    (alembic_dir / "versions").mkdir(parents=True)
    shutil.copy(_REAL_ALEMBIC_DIR / "env.py", alembic_dir / "env.py")
    shutil.copy(_REAL_ALEMBIC_DIR / "script.py.mako", alembic_dir / "script.py.mako")

    url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()

    config = Config()
    config.set_main_option("script_location", str(alembic_dir))
    return config


def _versions_dir(config: Config) -> Path:
    return Path(config.get_main_option("script_location") or "") / "versions"


def _latest_script(config: Config) -> str:
    generated = max(_versions_dir(config).glob("*.py"), key=lambda p: p.stat().st_mtime)
    return generated.read_text()


def _creates_table(script: str, table_name: str) -> bool:
    # Anchored to the create_table() call itself (rather than any mention of
    # the name) so a FK column like organization.plan_id -- which renders as
    # `['plan_id'], ['plan.id']` inside an unrelated, always-created table --
    # doesn't produce a false positive.
    return re.search(rf"""create_table\(\s*['"]{table_name}['"]""", script) is not None


@pytest.mark.module_dependent
def test_autogenerate_includes_billing_tables_when_enabled(blank_alembic_config: Config) -> None:
    # Blank DB, billing enabled (default product config) -- the very first
    # autogenerated revision must propose billing's tables alongside core's.
    command.revision(blank_alembic_config, message="initial", autogenerate=True)

    script = _latest_script(blank_alembic_config)
    assert _creates_table(script, "plan")
    assert _creates_table(script, "subscription")
    assert _creates_table(script, "webhook_event")


def test_autogenerate_excludes_billing_tables_when_disabled(
    blank_alembic_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(product_config, "MODULES", _DISABLE_BILLING)

    command.revision(blank_alembic_config, message="initial", autogenerate=True)

    script = _latest_script(blank_alembic_config)
    assert not _creates_table(script, "plan")
    assert not _creates_table(script, "subscription")
    assert not _creates_table(script, "webhook_event")
    assert _creates_table(script, "consent_record")  # gdpr unaffected


@pytest.mark.module_dependent
def test_autogenerate_does_not_drop_billing_tables_once_disabled(
    blank_alembic_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    # First revision: billing enabled, schema includes plan/subscription/
    # webhook_event, applied to the DB.
    command.revision(blank_alembic_config, message="initial", autogenerate=True)
    command.upgrade(blank_alembic_config, "head")

    # Disabling billing afterwards must not make the next autogenerate
    # propose dropping the tables that are still physically present.
    monkeypatch.setattr(product_config, "MODULES", _DISABLE_BILLING)
    module_loader.get_modules.cache_clear()
    command.revision(blank_alembic_config, message="billing_disabled", autogenerate=True)

    script = _latest_script(blank_alembic_config)
    assert "drop_table" not in script
    assert not _creates_table(script, "plan")
    assert not _creates_table(script, "subscription")
    assert not _creates_table(script, "webhook_event")


def test_autogenerate_never_proposes_the_template_scaffolds_table(
    blank_alembic_config: Config,
) -> None:
    # T-250. The import at the top of this file is what makes this real:
    # app/modules/_template is never imported by the application, but
    # importing it anywhere in the process is enough to register `Item` on the
    # SQLModel.metadata that alembic/env.py hands autogenerate as
    # target_metadata -- and something does exactly that (test_read_replica.py
    # sweeps every app.modules.*.repository through pkgutil). The scaffold is
    # not a module: its table must never be proposed, whatever else is
    # enabled.
    assert TemplateItem.__tablename__ in SQLModel.metadata.tables

    command.revision(blank_alembic_config, message="initial", autogenerate=True)

    script = _latest_script(blank_alembic_config)
    assert not _creates_table(script, TemplateItem.__tablename__)
    assert "__MODULE_NAME__" not in script
    assert _creates_table(script, "organization")  # ...and everything real still is
