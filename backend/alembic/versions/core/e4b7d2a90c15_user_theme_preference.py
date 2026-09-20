"""user_theme_preference

Revision ID: e4b7d2a90c15
Revises: c8a51d7f3b24
Create Date: 2026-09-20 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
# Chains onto c8a51d7f3b24 on the "core" branch via down_revision -- only a
# branch's *first* migration declares branch_labels (see
# app/core/migrations.py's module docstring).
revision: str = "e4b7d2a90c15"
down_revision: str | None = "c8a51d7f3b24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # F-004. NOT NULL with a server_default, in that order, because the column
    # is added to a populated table: without the default every existing row
    # would have to be NULL for an instant, which a NOT NULL column forbids,
    # and the ALTER would fail on any non-empty `user` table.
    #
    # The server_default is kept afterwards rather than dropped. The model
    # supplies its own Python-side default, so the database one is redundant
    # for anything going through SQLModel -- but it is not redundant for a
    # plain `INSERT INTO "user"` in a fixture, a psql session or a restore
    # from a dump taken before this revision, and "the column has a sensible
    # value however the row arrived" is worth one line of DDL.
    op.add_column(
        "user",
        sa.Column("theme", sa.String(), nullable=False, server_default="system"),
    )


def downgrade() -> None:
    op.drop_column("user", "theme")
