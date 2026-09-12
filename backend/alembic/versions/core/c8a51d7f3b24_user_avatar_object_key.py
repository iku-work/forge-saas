"""user_avatar_object_key

Revision ID: c8a51d7f3b24
Revises: a7e4c2f91b60
Create Date: 2026-09-12 09:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
# Chains onto a7e4c2f91b60 on the "core" branch via down_revision -- only a
# branch's *first* migration declares branch_labels (see
# app/core/migrations.py's module docstring).
revision: str = "c8a51d7f3b24"
down_revision: str | None = "a7e4c2f91b60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No foreign key to stored_file.id on purpose: `file_storage` is an
    # optional module, so that table is absent in the minimum-surface
    # configuration and the constraint would fail to create there. See
    # User.avatar_object_key.
    op.add_column("user", sa.Column("avatar_object_key", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("user", "avatar_object_key")
