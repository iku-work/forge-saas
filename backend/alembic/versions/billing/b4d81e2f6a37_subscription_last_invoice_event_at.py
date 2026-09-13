"""subscription_last_invoice_event_at

Revision ID: b4d81e2f6a37
Revises: a7c3e91f5d20
Create Date: 2026-09-13 21:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
# Chains onto a7c3e91f5d20 on the "billing" branch via down_revision -- only
# a branch's *first* migration declares branch_labels (see
# app/core/migrations.py's module docstring).
revision: str = "b4d81e2f6a37"
down_revision: str | None = "a7c3e91f5d20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Splits the staleness watermark in two: a7c3e91f5d20 added a single
    # column and compared subscription events against invoice events, whose
    # `created` timestamps describe different objects and are not ordered
    # relative to each other. Nullable with no backfill, same as its
    # predecessor: NULL means "no invoice event applied yet".
    op.add_column(
        "subscription",
        sa.Column("last_invoice_event_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("subscription", "last_invoice_event_at")
