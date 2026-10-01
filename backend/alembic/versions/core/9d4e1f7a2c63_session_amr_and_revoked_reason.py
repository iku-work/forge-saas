"""session_amr_and_revoked_reason

Revision ID: 9d4e1f7a2c63
Revises: e4b7d2a90c15
Create Date: 2026-09-29 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
# Chains onto e4b7d2a90c15 on the "core" branch (and moves
# CORE_HEAD_REVISION, updated alongside this file) -- review R3: the admin
# gate checks which factors the *session* passed, not which the account has
# enrolled; and why a session was revoked, so only a rotated-away refresh
# token is treated as stolen when it comes back.
revision: str = "9d4e1f7a2c63"
down_revision: str | None = "e4b7d2a90c15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing sessions get '[]': nothing is known about how they were
    # established, which is exactly the fact the admin gate must not assume
    # its way past. The consequence is deliberate and accepted (plan D3):
    # every superadmin signs in once more after this ships, through a flow
    # that records its factors. Ordinary users notice nothing -- no other
    # check reads this column.
    op.add_column(
        "session",
        sa.Column(
            "amr",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    # Nullable, no default: rows revoked before this existed have no known
    # reason, and AuthService.refresh treats NULL the way it treated every
    # revocation until now (as rotation) -- the conservative reading.
    op.add_column("session", sa.Column("revoked_reason", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("session", "revoked_reason")
    op.drop_column("session", "amr")
