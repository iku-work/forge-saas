import asyncio
from logging.config import fileConfig
from typing import Literal

from alembic import context
from alembic.autogenerate.api import AutogenContext
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.sql.schema import SchemaItem
from sqlmodel import SQLModel

from app.core.config import get_settings
from app.core.encrypted_type import EncryptedString
from app.core.module_loader import get_modules

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata

# §0.6 / law: the DB URL comes from Settings (pydantic-settings), never from
# alembic.ini or os.environ directly.
#
# T-244: migrations_database_url, not DATABASE_URL -- identical to it unless
# MIGRATIONS_DATABASE_URL is set, which a deployment fronted by a
# transaction-mode pgbouncer must do. Alembic guards the version table with a
# session-level advisory lock and runs multi-statement DDL; transaction
# pooling can hand each of those a different Postgres backend. See that
# property's docstring and docs/adr/0002-pgbouncer.md.
config.set_main_option("sqlalchemy.url", get_settings().migrations_database_url)

# T-062: tables owned by an optional module. Used by include_object below so
# autogenerate never proposes a CREATE for a disabled module's table (model
# not imported -> not on metadata anyway) nor a DROP for one that is still
# physically present in the DB from a time the module was enabled.
MODULE_TABLES: dict[str, set[str]] = {
    "billing": {"plan", "subscription", "webhook_event", "dunning_campaign", "usage_record"},
    "gdpr": {"consent_record", "data_export_job"},
    "notifications": {"notification", "push_token"},
    "file_storage": {"stored_file"},
}


def load_models_for_migration() -> None:
    """Register only enabled modules' models on SQLModel.metadata.

    Unlike app.models (imported eagerly everywhere else in the app, where
    every module's tables always existing on metadata is harmless), this is
    what autogenerate diffs against -- importing a disabled module's models
    here would make `alembic revision --autogenerate` propose creating
    tables for a module the product turned off.
    """
    # core — always
    import app.modules.audit.models  # noqa: PLC0415
    import app.modules.auth.models  # noqa: PLC0415
    import app.modules.tenancy.models  # noqa: PLC0415
    import app.workers.jobs.records  # noqa: PLC0415

    modules = get_modules()
    if modules.billing.enabled:
        import app.modules.billing.models  # noqa: PLC0415
        import app.modules.billing.usage_models  # noqa: PLC0415
    if modules.gdpr.enabled:
        import app.modules.gdpr.models  # noqa: PLC0415
    if modules.notifications.enabled:
        import app.modules.notifications.models  # noqa: PLC0415
    if modules.file_storage.enabled:
        import app.modules.storage.models  # noqa: PLC0415


load_models_for_migration()


def _enabled_for_table(table_name: str | None) -> bool | None:
    """None if table_name doesn't belong to any optional module."""
    modules = get_modules()
    for module_name, tables in MODULE_TABLES.items():
        if table_name in tables:
            return bool(getattr(modules, module_name).enabled)
    return None


def include_object(
    object_: SchemaItem,
    name: str | None,
    type_: str,
    reflected: bool,  # noqa: ARG001 — required by alembic's include_object signature
    compare_to: SchemaItem | None,  # noqa: ARG001 — required by alembic's include_object signature
) -> bool:
    """Exclude a disabled module's tables (and FKs pointing at them) from
    autogenerate comparison.

    Tables still physically present in the DB (e.g. created while the module
    was enabled, then disabled later) would otherwise show up as an
    unexpected DROP -- load_models_for_migration() already keeps a disabled
    module's tables off target_metadata, but that alone only handles the
    table itself. A core table's column can still hold a `Field(foreign_key=
    "plan.id")`-style reference (e.g. Organization.plan_id -> Plan), which
    needs its own exclusion or autogenerate proposes an FK to a table that
    was never created.
    """
    if type_ == "table":
        enabled = _enabled_for_table(name)
        return True if enabled is None else enabled
    if type_ == "foreign_key_constraint":
        referred_table = getattr(object_, "referred_table", None)
        enabled = _enabled_for_table(getattr(referred_table, "name", None))
        return True if enabled is None else enabled
    return True


def render_item(type_: str, obj: object, autogen_context: AutogenContext) -> "str | Literal[False]":
    """Render EncryptedString with the import it needs.

    Alembic's default renderer prints the fully-qualified dotted path
    (``app.core.encrypted_type.EncryptedString()``) for unrecognised column
    types but never adds the corresponding import, so the generated script
    raises NameError the moment it runs. Returning a short name here and
    registering the import on autogen_context.imports makes it land in the
    script's ``${imports}`` block instead.
    """
    if type_ == "type" and isinstance(obj, EncryptedString):
        autogen_context.imports.add("from app.core.encrypted_type import EncryptedString")
        return "EncryptedString()"
    return False


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_item=render_item,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_item=render_item,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
