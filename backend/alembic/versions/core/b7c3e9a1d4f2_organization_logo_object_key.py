"""organization_logo_object_key

Revision ID: b7c3e9a1d4f2
Revises: 9d4e1f7a2c63
Create Date: 2026-10-10 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
# Chains onto 9d4e1f7a2c63 on the "core" branch via down_revision -- only a
# branch's *first* migration declares branch_labels (see
# app/core/migrations.py's module docstring).
revision: str = "b7c3e9a1d4f2"
down_revision: str | None = "9d4e1f7a2c63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The private object key of an uploaded logo, like user.avatar_object_key:
    # nullable (most organizations have none) and deliberately not a foreign
    # key to stored_file, which only exists when file_storage is on.
    op.add_column("organization", sa.Column("logo_object_key", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("organization", "logo_object_key")
