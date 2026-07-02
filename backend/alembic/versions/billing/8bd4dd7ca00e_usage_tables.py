"""usage_tables

Revision ID: 8bd4dd7ca00e
Revises: cbd24d388291
Create Date: 2026-07-02 00:00:00.000000

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
# T-132: third migration on the "billing" branch -- chains onto
# cbd24d388291 via down_revision; only a branch's *first* migration declares
# branch_labels (see app/core/migrations.py's module docstring).
revision: str = "8bd4dd7ca00e"
down_revision: str | None = "cbd24d388291"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_record",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("metric", sa.String(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reported_to_stripe_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stripe_usage_record_id", sa.String(), nullable=True),
        sa.Column("idempotency_key", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(op.f("ix_usage_record_org_id"), "usage_record", ["org_id"], unique=False)
    op.create_index(op.f("ix_usage_record_metric"), "usage_record", ["metric"], unique=False)
    op.create_index(
        op.f("ix_usage_record_occurred_at"), "usage_record", ["occurred_at"], unique=False
    )
    op.create_index(
        op.f("ix_usage_record_idempotency_key"),
        "usage_record",
        ["idempotency_key"],
        unique=True,
    )
    # Read path report_usage_to_stripe actually runs: unreported records
    # grouped by (org_id, metric). Not covered by the individual single-column
    # indexes above.
    op.create_index(
        "ix_usage_record_reported_to_stripe_at_org_id_metric",
        "usage_record",
        ["reported_to_stripe_at", "org_id", "metric"],
        unique=False,
    )
    op.add_column(
        "plan",
        sa.Column(
            "usage_meter_event_names",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    # Drop the server_default once existing rows are backfilled -- SQLModel's
    # default_factory=dict handles new rows from here on; the column should
    # not silently keep inserting '{}' outside the ORM's control.
    op.alter_column("plan", "usage_meter_event_names", server_default=None)


def downgrade() -> None:
    op.drop_column("plan", "usage_meter_event_names")
    op.drop_index("ix_usage_record_reported_to_stripe_at_org_id_metric", table_name="usage_record")
    op.drop_index(op.f("ix_usage_record_idempotency_key"), table_name="usage_record")
    op.drop_index(op.f("ix_usage_record_occurred_at"), table_name="usage_record")
    op.drop_index(op.f("ix_usage_record_metric"), table_name="usage_record")
    op.drop_index(op.f("ix_usage_record_org_id"), table_name="usage_record")
    op.drop_table("usage_record")
