"""audit_event_severity

Revision ID: d1668e754708
Revises: 5e0f22f854e6
Create Date: 2026-07-03 00:00:00.000000

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
# T-141: new tip of the core trunk (see app/core/migrations.py's
# CORE_HEAD_REVISION, updated alongside this file) -- severity is consulted
# by the admin panel's audit-alerting queries, not by any optional module.
revision: str = "d1668e754708"
down_revision: str | None = "5e0f22f854e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "audit_event",
        sa.Column(
            "severity",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=False,
            server_default="info",
        ),
    )
    op.create_index(op.f("ix_audit_event_severity"), "audit_event", ["severity"], unique=False)


def downgrade() -> None:
    # §0.3: every migration must have a working downgrade() — this is not
    # optional boilerplate. CI runs upgrade -> downgrade -> upgrade and fails
    # the build if this function doesn't reverse upgrade() cleanly.
    op.drop_index(op.f("ix_audit_event_severity"), table_name="audit_event")
    op.drop_column("audit_event", "severity")
