"""verification_token_password

Revision ID: c4d8f2a6b1e3
Revises: b7c3e9a1d4f2
Create Date: 2026-11-02 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
# Chains onto b7c3e9a1d4f2 on the "core" branch via down_revision -- only a
# branch's *first* migration declares branch_labels (see
# app/core/migrations.py's module docstring).
revision: str = "c4d8f2a6b1e3"
down_revision: str | None = "b7c3e9a1d4f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "email_verification_token",
        sa.Column("pending_password_hash", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("email_verification_token", "pending_password_hash")
