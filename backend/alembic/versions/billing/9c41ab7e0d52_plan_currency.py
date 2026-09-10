"""plan_currency

Revision ID: 9c41ab7e0d52
Revises: 4f1c8ab27d93
Create Date: 2026-09-10 13:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
# Chains onto 4f1c8ab27d93 on the "billing" branch via down_revision -- only
# a branch's *first* migration declares branch_labels (see
# app/core/migrations.py's module docstring).
revision: str = "9c41ab7e0d52"
down_revision: str | None = "4f1c8ab27d93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 'usd' as the backfill because that is what every existing row already
    # meant: price_monthly/price_yearly were documented as USD cents and the
    # frontends hardcoded a "$". Rows whose Stripe price is actually in
    # another currency are corrected by the next `make sync-plans`, which
    # reads the currency off the resolved price.
    op.add_column(
        "plan",
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="usd"),
    )
    # Dropped for the same reason as usage_prices_micros: new rows get their
    # value from the model, not from the database quietly supplying one.
    op.alter_column("plan", "currency", server_default=None)


def downgrade() -> None:
    op.drop_column("plan", "currency")
