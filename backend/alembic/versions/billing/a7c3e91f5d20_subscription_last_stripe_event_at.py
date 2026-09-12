"""subscription_last_stripe_event_at

Revision ID: a7c3e91f5d20
Revises: 9c41ab7e0d52
Create Date: 2026-09-12 15:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
# Chains onto 9c41ab7e0d52 on the "billing" branch via down_revision -- only
# a branch's *first* migration declares branch_labels (see
# app/core/migrations.py's module docstring).
revision: str = "a7c3e91f5d20"
down_revision: str | None = "9c41ab7e0d52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable with no backfill on purpose: NULL means "no event applied
    # yet", which is exactly true of every existing row. Backfilling a
    # timestamp would claim an ordering this table never observed, and would
    # make the guard reject the next legitimate event for any row whose
    # invented value happened to be later than it.
    op.add_column(
        "subscription",
        sa.Column("last_stripe_event_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("subscription", "last_stripe_event_at")
