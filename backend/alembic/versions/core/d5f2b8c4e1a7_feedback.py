"""feedback

Revision ID: d5f2b8c4e1a7
Revises: a3c7e1f9b5d2
Create Date: 2026-11-17 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "d5f2b8c4e1a7"
down_revision: str | None = "a3c7e1f9b5d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Who to tell about new feedback: AuthService.superadmin_ids.
    op.create_index("ix_user_is_superadmin", "user", ["is_superadmin"])
    op.create_table(
        "feedback",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=True),
        sa.Column("category", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("message", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("page", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_feedback_user_id", "feedback", ["user_id"])
    op.create_index("ix_feedback_org_id", "feedback", ["org_id"])
    op.create_index(
        "ix_feedback_resolved_at_created_at_id", "feedback", ["resolved_at", "created_at", "id"]
    )


def downgrade() -> None:
    op.drop_index("ix_feedback_resolved_at_created_at_id", table_name="feedback")
    op.drop_index("ix_feedback_org_id", table_name="feedback")
    op.drop_index("ix_feedback_user_id", table_name="feedback")
    op.drop_table("feedback")
    op.drop_index("ix_user_is_superadmin", table_name="user")
