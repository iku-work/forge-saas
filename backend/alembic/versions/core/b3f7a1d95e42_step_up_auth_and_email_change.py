"""step_up_auth_and_email_change

Revision ID: b3f7a1d95e42
Revises: 8c736033a2c5
Create Date: 2026-07-09 15:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
# T-218: new tip of the core trunk (see app/core/migrations.py's
# CORE_HEAD_REVISION, updated alongside this file) -- step-up re-auth and
# TOTP replay tracking extend the always-on Session/TOTPSecret tables, and
# EmailChangeToken is a user-owned auth primitive alongside the other
# single-use token tables, not an optional module.
revision: str = "b3f7a1d95e42"
down_revision: str | None = "8c736033a2c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # session.auth_time: added nullable first so existing rows can be
    # backfilled, then tightened to NOT NULL -- see Session model's
    # docstring (app/modules/auth/models.py) for what this column means.
    # created_at is the best available approximation of "when this session's
    # credentials were actually verified" for rows that predate this column.
    op.add_column("session", sa.Column("auth_time", sa.DateTime(timezone=True), nullable=True))
    op.execute(sa.text("UPDATE session SET auth_time = created_at WHERE auth_time IS NULL"))
    op.alter_column("session", "auth_time", nullable=False)

    op.add_column("totp_secret", sa.Column("last_consumed_step", sa.Integer(), nullable=True))

    op.create_table(
        "email_change_token",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("new_email", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("token_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        op.f("ix_email_change_token_user_id"), "email_change_token", ["user_id"], unique=False
    )


def downgrade() -> None:
    # §0.3: every migration must have a working downgrade() — this is not
    # optional boilerplate. CI runs upgrade -> downgrade -> upgrade and fails
    # the build if this function doesn't reverse upgrade() cleanly.
    op.drop_index(op.f("ix_email_change_token_user_id"), table_name="email_change_token")
    op.drop_table("email_change_token")
    op.drop_column("totp_secret", "last_consumed_step")
    op.drop_column("session", "auth_time")
