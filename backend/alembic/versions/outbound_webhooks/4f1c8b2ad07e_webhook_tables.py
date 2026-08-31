"""webhook_endpoint + webhook_delivery

Revision ID: 4f1c8b2ad07e
Revises: a7e4c2f91b60
Create Date: 2026-08-31 10:20:44.118902

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.encrypted_type import EncryptedString

# revision identifiers, used by Alembic.
# T-320: the first migration on the `outbound_webhooks` branch, which branches
# off the core trunk's tip rather than chaining after another module -- see
# app/core/migrations.py for why each optional module gets its own branch
# label. The module ships off, so nothing here is applied unless
# product/config.py enables it.
revision: str = "4f1c8b2ad07e"
down_revision: str | None = "a7e4c2f91b60"
branch_labels: str | Sequence[str] | None = "outbound_webhooks"
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "webhook_endpoint",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("url", sqlmodel.sql.sqltypes.AutoString(length=2048), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=True),
        # §7's rule for a `*_secret` column, and the one place in this schema
        # where encryption rather than hashing is the *requirement*: signing
        # an outbound request means computing an HMAC with this value, so a
        # digest would make the column useless.
        sa.Column("secret", EncryptedString(), nullable=False),
        sa.Column("secret_previous", EncryptedString(), nullable=True),
        sa.Column("secret_previous_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_types", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        # Nullable and no ondelete, exactly as `api_key.created_by` is: T-036's
        # purge job nulls this column by hand before deleting the user, because
        # erasing a person must not take an organization's live integration
        # with them.
        sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_webhook_endpoint_created_by"), "webhook_endpoint", ["created_by"], unique=False
    )
    op.create_index(
        op.f("ix_webhook_endpoint_enabled"), "webhook_endpoint", ["enabled"], unique=False
    )
    op.create_index(
        op.f("ix_webhook_endpoint_org_id"), "webhook_endpoint", ["org_id"], unique=False
    )
    # §7: the FK's own index, created in the same migration as the FK. This is
    # also the dispatch query's index -- "every enabled endpoint in this org"
    # runs on every emitted event.
    op.create_index(
        "ix_webhook_endpoint_org_id_created_at_id",
        "webhook_endpoint",
        ["org_id", "created_at", "id"],
        unique=False,
    )

    op.create_table(
        "webhook_delivery",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("endpoint_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("response_code", sa.Integer(), nullable=True),
        sa.Column("response_body", sqlmodel.sql.sqltypes.AutoString(length=2048), nullable=True),
        sa.Column("error", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_id", sa.Uuid(), nullable=False),
        sa.Column("replay_of_id", sa.Uuid(), nullable=True),
        # CASCADE at the database level: deleting an endpoint must take its log
        # with it, because a delivery row pointing at nothing can answer no
        # question anyone would ask of it.
        sa.ForeignKeyConstraint(["endpoint_id"], ["webhook_endpoint.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["replay_of_id"], ["webhook_delivery.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_webhook_delivery_delivery_id"), "webhook_delivery", ["delivery_id"], unique=False
    )
    op.create_index(
        op.f("ix_webhook_delivery_endpoint_id"), "webhook_delivery", ["endpoint_id"], unique=False
    )
    # The delivery log's page (T-322): one endpoint's attempts, newest first,
    # by keyset -- never OFFSET (§0.3).
    op.create_index(
        "ix_webhook_delivery_endpoint_id_created_at_id",
        "webhook_delivery",
        ["endpoint_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_webhook_delivery_event_type"), "webhook_delivery", ["event_type"], unique=False
    )
    op.create_index(
        op.f("ix_webhook_delivery_org_id"), "webhook_delivery", ["org_id"], unique=False
    )
    # The retention sweep's index (T-321), and the org-wide log's.
    op.create_index(
        "ix_webhook_delivery_org_id_created_at",
        "webhook_delivery",
        ["org_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_webhook_delivery_replay_of_id"), "webhook_delivery", ["replay_of_id"], unique=False
    )
    op.create_index(
        op.f("ix_webhook_delivery_status"), "webhook_delivery", ["status"], unique=False
    )
    # What the retry sweep asks for: rows that are due. `status` leads because
    # `pending` is a small and shrinking slice of a table whose bulk is
    # `delivered`.
    op.create_index(
        "ix_webhook_delivery_status_next_attempt_at",
        "webhook_delivery",
        ["status", "next_attempt_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_webhook_delivery_status_next_attempt_at", table_name="webhook_delivery")
    op.drop_index(op.f("ix_webhook_delivery_status"), table_name="webhook_delivery")
    op.drop_index(op.f("ix_webhook_delivery_replay_of_id"), table_name="webhook_delivery")
    op.drop_index("ix_webhook_delivery_org_id_created_at", table_name="webhook_delivery")
    op.drop_index(op.f("ix_webhook_delivery_org_id"), table_name="webhook_delivery")
    op.drop_index(op.f("ix_webhook_delivery_event_type"), table_name="webhook_delivery")
    op.drop_index("ix_webhook_delivery_endpoint_id_created_at_id", table_name="webhook_delivery")
    op.drop_index(op.f("ix_webhook_delivery_endpoint_id"), table_name="webhook_delivery")
    op.drop_index(op.f("ix_webhook_delivery_delivery_id"), table_name="webhook_delivery")
    op.drop_table("webhook_delivery")
    op.drop_index("ix_webhook_endpoint_org_id_created_at_id", table_name="webhook_endpoint")
    op.drop_index(op.f("ix_webhook_endpoint_org_id"), table_name="webhook_endpoint")
    op.drop_index(op.f("ix_webhook_endpoint_enabled"), table_name="webhook_endpoint")
    op.drop_index(op.f("ix_webhook_endpoint_created_by"), table_name="webhook_endpoint")
    op.drop_table("webhook_endpoint")
