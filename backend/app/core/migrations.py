"""Drives `alembic upgrade` so only enabled modules' tables ever get created.

Every module's migration scripts ship with the template regardless of
product.config.py (T-058: only files listed in TEMPLATE.md as product-owned
are excluded). What's conditional is which `alembic upgrade` *target* this
module ever asks Alembic to reach. Core migrations (auth, tenancy, audit,
job_record) form a single trunk; each optional module's first migration
branches off the trunk's tip with its own `branch_labels` (see
alembic/versions/billing/0d2ed3456e9c_billing_tables.py,
alembic/versions/gdpr/c03944c55a6e_gdpr_tables.py,
alembic/versions/notifications/3ec4d8dcd8d1_notifications_tables.py, and
alembic/versions/file_storage/29ffe2c49374_stored_files.py). Asking Alembic for
"<branch>@head" walks only that module's chain plus any unapplied trunk
ancestors -- a branch nobody ever asks for is simply never recorded in
alembic_version, so flipping the module on later and re-running upgrade()
applies it from scratch, exactly as if the DB had never seen it.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from alembic import command
from alembic.config import Config

from app.core.module_loader import get_modules

if TYPE_CHECKING:
    from app.core.modules import ModuleName

# Revision ID of the last always-on ("core") migration. Update this constant
# whenever a new core-only migration is appended to alembic/versions/core/.
CORE_HEAD_REVISION = "5e0f22f854e6"

# Module name -> the alembic branch label its first migration declares.
# Only modules that currently ship a models.py (and therefore a migration)
# are listed here.
MODULE_BRANCHES: dict[ModuleName, str] = {
    "billing": "billing",
    "gdpr": "gdpr",
    "notifications": "notifications",
    "file_storage": "file_storage",
}

_BACKEND_DIR = Path(__file__).resolve().parents[2]
ALEMBIC_INI = _BACKEND_DIR / "alembic.ini"


def build_config() -> Config:
    """The same Alembic Config every entrypoint (CLI, tests) should use.

    Passing the .ini path explicitly makes `%(here)s` in version_locations
    resolve to backend/alembic.ini's own directory, regardless of the
    caller's current working directory.
    """
    return Config(str(ALEMBIC_INI))


def migration_targets() -> list[str]:
    """Ordered `alembic upgrade` targets that reach the schema for the
    currently active product.config.py modules."""
    targets = [CORE_HEAD_REVISION]
    modules = get_modules()
    for module_name, branch in MODULE_BRANCHES.items():
        if getattr(modules, module_name).enabled:
            targets.append(f"{branch}@head")
    return targets


def upgrade(config: Config) -> None:
    for target in migration_targets():
        command.upgrade(config, target)


def plan_summary() -> str:
    """Human-readable module-by-module summary for `make migrate-plan`."""
    modules = get_modules()
    lines = ["core            -> always applied (auth, tenancy, audit, job_record)"]
    for module_name in MODULE_BRANCHES:
        module_config = getattr(modules, module_name)
        state = "enabled  -> applying" if module_config.enabled else "disabled -> skipped"
        lines.append(f"{module_name:<15} -> {state}")
    return "\n".join(lines)


def main() -> None:
    import sys  # noqa: PLC0415 — only needed for the CLI entrypoint, not the library API

    if len(sys.argv) > 1 and sys.argv[1] == "plan":
        print(plan_summary())  # noqa: T201 — this *is* the CLI output
        return
    upgrade(build_config())


if __name__ == "__main__":
    main()
