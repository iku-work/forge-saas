"""membership_granted_permissions

Revision ID: f7b1d3e5a9c2
Revises: e5a9c3d7f1b2
Create Date: 2026-11-11 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f7b1d3e5a9c2"
down_revision: str | None = "e5a9c3d7f1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Permissions granted on top of the role. The server default is what a
    # new membership starts with, and it fills the rows that already exist,
    # so every member can still upload files the moment this lands. No
    # index: it is only read off a membership row already fetched.
    op.add_column(
        "membership",
        sa.Column(
            "granted_permissions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[\"files:write\"]'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("membership", "granted_permissions")
