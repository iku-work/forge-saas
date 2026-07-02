"""notification_idempotency_key

Revision ID: 18ce46dbc1a1
Revises: 3ec4d8dcd8d1
Create Date: 2026-06-28 00:00:00.000000

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
# T-102: second migration on the "notifications" branch -- chains onto
# 3ec4d8dcd8d1 via down_revision; only a branch's *first* migration declares
# branch_labels (see app/core/migrations.py's module docstring).
revision: str = "18ce46dbc1a1"
down_revision: str | None = "3ec4d8dcd8d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "notification",
        sa.Column("idempotency_key", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.create_index(
        "ix_notification_org_id_user_id_idempotency_key",
        "notification",
        ["org_id", "user_id", "idempotency_key"],
        unique=False,
    )


def downgrade() -> None:
    # §0.3: every migration must have a working downgrade() — this is not
    # optional boilerplate. CI runs upgrade -> downgrade -> upgrade and fails
    # the build if this function doesn't reverse upgrade() cleanly.
    op.drop_index("ix_notification_org_id_user_id_idempotency_key", table_name="notification")
    op.drop_column("notification", "idempotency_key")
