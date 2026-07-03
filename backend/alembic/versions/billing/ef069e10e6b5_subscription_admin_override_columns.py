"""subscription_admin_override_columns

Revision ID: ef069e10e6b5
Revises: 8bd4dd7ca00e
Create Date: 2026-07-03 00:00:00.000000

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
# T-141: new tip of the "billing" branch (see app/core/migrations.py's
# MODULE_BRANCHES) -- these three columns back the admin panel's
# force-assign-plan / extend-trial support-ops endpoints.
revision: str = "ef069e10e6b5"
down_revision: str | None = "8bd4dd7ca00e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "subscription",
        sa.Column("billing_interval", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.add_column(
        "subscription",
        sa.Column("free_override", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "subscription",
        sa.Column("override_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    # §0.3: every migration must have a working downgrade() — this is not
    # optional boilerplate. CI runs upgrade -> downgrade -> upgrade and fails
    # the build if this function doesn't reverse upgrade() cleanly.
    op.drop_column("subscription", "override_expires_at")
    op.drop_column("subscription", "free_override")
    op.drop_column("subscription", "billing_interval")
