"""usage_prices

Revision ID: 53d575b63324
Revises: ef069e10e6b5
Create Date: 2026-07-19 18:55:07.972415

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
# Chains onto ef069e10e6b5 on the "billing" branch via down_revision -- only
# a branch's *first* migration declares branch_labels (see
# app/core/migrations.py's module docstring).
revision: str = "53d575b63324"
down_revision: str | None = "ef069e10e6b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "plan",
        sa.Column(
            "usage_prices_micros",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    # Drop the server_default once existing rows are backfilled -- SQLModel's
    # default_factory=dict handles new rows from here on; the column should
    # not silently keep inserting '{}' outside the ORM's control.
    op.alter_column("plan", "usage_prices_micros", server_default=None)


def downgrade() -> None:
    op.drop_column("plan", "usage_prices_micros")
