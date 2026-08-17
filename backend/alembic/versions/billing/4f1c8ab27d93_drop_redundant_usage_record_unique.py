"""drop_redundant_usage_record_unique

Revision ID: 4f1c8ab27d93
Revises: 53d575b63324
Create Date: 2026-08-16 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
# Chains onto 53d575b63324 on the "billing" branch via down_revision -- only
# a branch's *first* migration declares branch_labels (see
# app/core/migrations.py's module docstring).
revision: str = "4f1c8ab27d93"
down_revision: str | None = "53d575b63324"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 8bd4dd7ca00e (T-132) enforced UsageRecord.idempotency_key's uniqueness
# twice: a table-level `sa.UniqueConstraint("idempotency_key")` *and* a
# `create_index(..., unique=True)`. The model declares
# `Field(unique=True, index=True)`, which SQLModel renders as the unique index
# alone -- so the constraint exists in every database that ran that migration
# and in no model's metadata.
#
# Two consequences, one cosmetic and one not:
#   * `alembic revision --autogenerate` proposed dropping it on every run,
#     which is how a real diff gets lost in noise nobody reads any more;
#   * Postgres backs a UNIQUE CONSTRAINT with its own btree, so every insert
#     maintained two identical unique indexes on the same column.
#
# Dropping the constraint leaves `ix_usage_record_idempotency_key` (UNIQUE)
# doing the enforcing, so the invariant that docstring calls "a genuine
# DB-level unique constraint" is unchanged -- a retried metered action still
# cannot double-record. The redundant *object* goes, not the guarantee.
_CONSTRAINT = "usage_record_idempotency_key_key"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "usage_record", type_="unique")


def downgrade() -> None:
    op.create_unique_constraint(_CONSTRAINT, "usage_record", ["idempotency_key"])
