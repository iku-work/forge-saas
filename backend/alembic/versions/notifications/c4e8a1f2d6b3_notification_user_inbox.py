"""notification_user_inbox

Revision ID: c4e8a1f2d6b3
Revises: 7b41ad0c9e52
Create Date: 2026-11-14 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
# Fourth migration on the "notifications" branch. A notification now belongs
# to its recipient: org_id becomes optional, and the two composite indexes
# lose their org_id prefix because every read is by user.
revision: str = "c4e8a1f2d6b3"
down_revision: str | None = "7b41ad0c9e52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("notification", "org_id", existing_type=sa.Uuid(), nullable=True)
    op.drop_index("ix_notification_org_id_user_id_created_at_id", table_name="notification")
    op.drop_index("ix_notification_org_id_user_id_idempotency_key", table_name="notification")
    op.create_index(
        "ix_notification_user_id_created_at_id",
        "notification",
        ["user_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_notification_user_id_idempotency_key",
        "notification",
        ["user_id", "idempotency_key"],
        unique=False,
    )


def downgrade() -> None:
    # §0.3: CI runs upgrade -> downgrade -> upgrade. A notification with no
    # org cannot exist under the old schema, so those rows are deleted before
    # the column becomes NOT NULL again. They are inbox items about the
    # person (invitations), not records anything else refers to.
    op.execute(sa.text("DELETE FROM notification WHERE org_id IS NULL"))
    op.drop_index("ix_notification_user_id_idempotency_key", table_name="notification")
    op.drop_index("ix_notification_user_id_created_at_id", table_name="notification")
    op.create_index(
        "ix_notification_org_id_user_id_idempotency_key",
        "notification",
        ["org_id", "user_id", "idempotency_key"],
        unique=False,
    )
    op.create_index(
        "ix_notification_org_id_user_id_created_at_id",
        "notification",
        ["org_id", "user_id", "created_at", "id"],
        unique=False,
    )
    op.alter_column("notification", "org_id", existing_type=sa.Uuid(), nullable=False)
