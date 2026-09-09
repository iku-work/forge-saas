"""api_key

Revision ID: d99a77da563c
Revises: a7e4c2f91b60
Create Date: 2026-08-30 13:52:11.402118

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
# T-310: the first migration on the `public_api` branch, which branches off
# the core trunk's tip rather than chaining after another module -- see
# app/core/migrations.py for why each optional module gets its own branch
# label. Nothing on this branch is applied unless product/config.py enables
# the module, so a product that never wants an API key never grows the table.
revision: str = "d99a77da563c"
down_revision: str | None = "a7e4c2f91b60"
branch_labels: str | Sequence[str] | None = "public_api"
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_key",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
        sa.Column("prefix", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("key_hash", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rate_limit_per_minute", sa.Integer(), nullable=True),
        # Nullable, and no ondelete: T-036's purge job nulls this column by
        # hand before it deletes the user, exactly as it already does for
        # Membership.invited_by -- erasing a person must not take an
        # organization's still-active credential with them.
        sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
        # CASCADE, and this one is at the database level on purpose: the
        # product exposes no org-delete endpoint, so the only thing that can
        # remove an organization row is an operator at a psql prompt, and a
        # live key outliving its tenant is the worst orphan this schema could
        # produce. A rule the database enforces holds whether or not the
        # person deleting the row knew about it.
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_api_key_created_by"), "api_key", ["created_by"], unique=False)
    op.create_index(op.f("ix_api_key_org_id"), "api_key", ["org_id"], unique=False)
    # §7: the FK's own index, created in the same migration as the FK.
    op.create_index(
        "ix_api_key_org_id_created_at_id",
        "api_key",
        ["org_id", "created_at", "id"],
        unique=False,
    )
    # UNIQUE, and the reason is not tidiness: this is the one column the
    # authentication path queries, once per request that presents a key, and
    # a duplicate prefix would make that lookup ambiguous rather than merely
    # slow. A collision is a failed insert, which is the correct outcome.
    op.create_index(op.f("ix_api_key_prefix"), "api_key", ["prefix"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_api_key_prefix"), table_name="api_key")
    op.drop_index("ix_api_key_org_id_created_at_id", table_name="api_key")
    op.drop_index(op.f("ix_api_key_org_id"), table_name="api_key")
    op.drop_index(op.f("ix_api_key_created_by"), table_name="api_key")
    op.drop_table("api_key")
