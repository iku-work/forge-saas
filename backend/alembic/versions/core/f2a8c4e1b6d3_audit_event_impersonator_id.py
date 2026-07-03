"""audit_event_impersonator_id

Revision ID: f2a8c4e1b6d3
Revises: d1668e754708
Create Date: 2026-07-03 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
# T-142: new tip of the core trunk (see app/core/migrations.py's
# CORE_HEAD_REVISION, updated alongside this file) -- impersonator_id is
# stamped by AuditService.log for every module's audit events, not just
# admin's, so it lives on the always-on core table rather than a
# module-scoped one.
revision: str = "f2a8c4e1b6d3"
down_revision: str | None = "d1668e754708"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "audit_event",
        sa.Column("impersonator_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        op.f("ix_audit_event_impersonator_id"), "audit_event", ["impersonator_id"], unique=False
    )


def downgrade() -> None:
    # §0.3: every migration must have a working downgrade() — this is not
    # optional boilerplate. CI runs upgrade -> downgrade -> upgrade and fails
    # the build if this function doesn't reverse upgrade() cleanly.
    op.drop_index(op.f("ix_audit_event_impersonator_id"), table_name="audit_event")
    op.drop_column("audit_event", "impersonator_id")
