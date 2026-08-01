"""email_change_token_cancel_and_fingerprint

Revision ID: f6a2e9c4d857
Revises: d11c2b8e16c1
Create Date: 2026-07-11 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
# T-226: new tip of the core trunk (see app/core/migrations.py's
# CORE_HEAD_REVISION, updated alongside this file) -- extends T-218's
# email_change_token table with a cancel-token column and a request-time
# device/IP fingerprint, both additive to an existing always-on table.
revision: str = "f6a2e9c4d857"
down_revision: str | None = "d11c2b8e16c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("email_change_token", sa.Column("cancel_token_hash", sa.String(), nullable=True))
    op.create_unique_constraint(
        "email_change_token_cancel_token_hash_key",
        "email_change_token",
        ["cancel_token_hash"],
    )
    op.add_column(
        "email_change_token", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True)
    )
    # requested_ip/requested_user_agent: added nullable first, backfilled for
    # any row that predates this column (there's no real fingerprint to
    # recover for those), then tightened to NOT NULL -- same two-step pattern
    # b3f7a1d95e42 used for session.auth_time.
    op.add_column("email_change_token", sa.Column("requested_ip", sa.String(), nullable=True))
    op.add_column(
        "email_change_token", sa.Column("requested_user_agent", sa.String(), nullable=True)
    )
    op.execute(
        sa.text("UPDATE email_change_token SET requested_ip = 'unknown' WHERE requested_ip IS NULL")
    )
    op.execute(
        sa.text(
            "UPDATE email_change_token SET requested_user_agent = 'unknown' "
            "WHERE requested_user_agent IS NULL"
        )
    )
    op.alter_column("email_change_token", "requested_ip", nullable=False)
    op.alter_column("email_change_token", "requested_user_agent", nullable=False)


def downgrade() -> None:
    # §0.3: every migration must have a working downgrade() — this is not
    # optional boilerplate. CI runs upgrade -> downgrade -> upgrade and fails
    # the build if this function doesn't reverse upgrade() cleanly.
    op.drop_column("email_change_token", "requested_user_agent")
    op.drop_column("email_change_token", "requested_ip")
    op.drop_column("email_change_token", "cancelled_at")
    op.drop_constraint(
        "email_change_token_cancel_token_hash_key",
        "email_change_token",
        type_="unique",
    )
    op.drop_column("email_change_token", "cancel_token_hash")
