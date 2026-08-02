"""notification_params

Revision ID: 7b41ad0c9e52
Revises: 18ce46dbc1a1
Create Date: 2026-08-02 00:00:00.000000

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
# T-234: third migration on the "notifications" branch -- chains onto
# 18ce46dbc1a1 via down_revision; only a branch's *first* migration declares
# branch_labels (see app/core/migrations.py's module docstring).
revision: str = "7b41ad0c9e52"
down_revision: str | None = "18ce46dbc1a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NOT NULL with a server_default so rows written before T-234 (whose
    # title/body were English literals baked in by the caller) get `{}` rather
    # than NULL -- app.workers.jobs.notifications.send_push treats an empty
    # params dict plus a type with no catalog entry as "re-render impossible,
    # fall back to the stored title/body", which is exactly right for them.
    op.add_column(
        "notification",
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    # §0.3: every migration must have a working downgrade() — this is not
    # optional boilerplate. CI runs upgrade -> downgrade -> upgrade and fails
    # the build if this function doesn't reverse upgrade() cleanly.
    op.drop_column("notification", "params")
