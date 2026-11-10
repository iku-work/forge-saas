"""user_avatar_pending_key

Revision ID: e5a9c3d7f1b2
Revises: c4d8f2a6b1e3
Create Date: 2026-11-10 21:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5a9c3d7f1b2"
down_revision: str | None = "c4d8f2a6b1e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The upload the scanner worker is processing into the user's avatar. No
    # index: it is only ever read off a row already fetched by id.
    op.add_column("user", sa.Column("avatar_pending_key", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("user", "avatar_pending_key")
