"""product_settings_singleton

Revision ID: a7e4c2f91b60
Revises: f6a2e9c4d857
Create Date: 2026-08-07 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
# T-263: new tip of the core trunk (see app/core/migrations.py's
# CORE_HEAD_REVISION, updated alongside this file). Core rather than a module
# branch because the maintenance middleware reads this table on every request
# regardless of which optional modules the product enables -- a deployment
# where this table did not exist would 500 on the read instead of degrading.
revision: str = "a7e4c2f91b60"
down_revision: str | None = "f6a2e9c4d857"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_settings",
        # No autoincrement: this is a singleton keyed on the literal 1, and a
        # sequence would quietly make a second row insertable.
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        # Belt to the singleton's braces: the application only ever addresses
        # id=1, but this makes a second row impossible at the database level
        # too -- including for the direct `UPDATE` an operator might run during
        # an incident when the admin UI is what is broken.
        sa.CheckConstraint("id = 1", name="product_settings_singleton"),
    )
    # The row itself, so nothing has to handle "no settings row yet". `now()`
    # rather than a bound parameter keeps this a pure-SQL statement that runs
    # identically offline (`alembic upgrade --sql`).
    op.execute(
        "INSERT INTO product_settings (id, settings, updated_at, updated_by) "
        "VALUES (1, '{}'::jsonb, now(), NULL) ON CONFLICT (id) DO NOTHING"
    )
    # updated_by is intentionally *not* a foreign key to user.id -- same
    # reasoning as audit_event.actor_id: the record of who last changed the
    # platform's operating state has to survive that operator's own account
    # being deleted. Nothing joins on it; the admin UI resolves the email
    # separately, and shows the raw id when the user is gone.


def downgrade() -> None:
    op.drop_table("product_settings")
