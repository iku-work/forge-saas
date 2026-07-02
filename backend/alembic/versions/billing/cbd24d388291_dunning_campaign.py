"""dunning_campaign

Revision ID: cbd24d388291
Revises: 0d2ed3456e9c
Create Date: 2026-07-02 00:00:00.000000

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "cbd24d388291"
down_revision: str | None = "0d2ed3456e9c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dunning_campaign",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column("invoice_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("scheduled_job_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscription.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_dunning_campaign_subscription_id"),
        "dunning_campaign",
        ["subscription_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_dunning_campaign_subscription_id"), table_name="dunning_campaign")
    op.drop_table("dunning_campaign")
