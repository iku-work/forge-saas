"""pg_stat_statements

Revision ID: a1c9f3e7b2d4
Revises: f2a8c4e1b6d3
Create Date: 2026-07-07 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
# T-205: new tip of the core trunk (see app/core/migrations.py's
# CORE_HEAD_REVISION, updated alongside this file) -- slow-query
# observability is infrastructure every deployment needs regardless of which
# optional modules are enabled, so it lives on the always-on core trunk
# rather than a module-scoped branch.
revision: str = "a1c9f3e7b2d4"
down_revision: str | None = "f2a8c4e1b6d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Raw SQL justified per §0.3: CREATE EXTENSION has no SQLAlchemy Core
    # expression. This only registers the catalog objects (views/functions);
    # it succeeds even without `shared_preload_libraries` set (needed so a
    # plain testcontainers Postgres can run this migration in tests). Actually
    # *collecting* stats additionally requires the library to be preloaded at
    # postmaster start -- see docker-compose.yml / docker-compose.prod.yml's
    # postgres `command`.
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_stat_statements"))


def downgrade() -> None:
    # §0.3: every migration must have a working downgrade().
    op.execute(sa.text("DROP EXTENSION IF EXISTS pg_stat_statements"))
