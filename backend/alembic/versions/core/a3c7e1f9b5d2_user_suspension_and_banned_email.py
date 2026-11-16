"""user_suspension_and_banned_email

Revision ID: a3c7e1f9b5d2
Revises: f7b1d3e5a9c2
Create Date: 2026-11-16 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "a3c7e1f9b5d2"
down_revision: str | None = "f7b1d3e5a9c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Set by a superadmin. No index: it is read off a user row already
    # fetched, never filtered on.
    op.add_column("user", sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True))
    # A SHA-256 of the address, so a ban outlasts the account without
    # keeping the address. The unique constraint is the lookup index.
    op.create_table(
        "banned_email",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("email_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email_hash"),
    )


def downgrade() -> None:
    op.drop_table("banned_email")
    op.drop_column("user", "suspended_at")
