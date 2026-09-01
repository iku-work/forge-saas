"""webhook org fk cascade

Revision ID: c7e4a1f0b382
Revises: 4f1c8b2ad07e
Create Date: 2026-09-01 09:40:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
# T-350. Both webhook tables reference `organization` without an ON DELETE
# rule, while `api_key` (T-310) and `sso_connection` (T-331) both CASCADE.
# T-310 wrote down why -- "a live credential outliving its tenant is the worst
# orphan this schema could produce" -- and `webhook_endpoint` holds a signing
# secret, which is exactly that. The practical effect today is not an orphan
# but a refusal: deleting an organization fails on this foreign key, so
# removing a customer is a puzzle instead of a DELETE.
revision: str = "c7e4a1f0b382"
down_revision: str | None = "4f1c8b2ad07e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Postgres' default constraint names (no naming_convention is configured on
# this metadata, so these are what CREATE TABLE produced in 4f1c8b2ad07e).
_ENDPOINT_FK = "webhook_endpoint_org_id_fkey"
_DELIVERY_FK = "webhook_delivery_org_id_fkey"


def upgrade() -> None:
    # An FK's ON DELETE rule cannot be altered in place -- Postgres has no
    # ALTER CONSTRAINT for it -- so each is dropped and recreated. Cheap: the
    # constraint is re-validated against existing rows, and the index it needs
    # (ix_webhook_*_org_id) already exists.
    op.drop_constraint(_ENDPOINT_FK, "webhook_endpoint", type_="foreignkey")
    op.create_foreign_key(
        _ENDPOINT_FK,
        "webhook_endpoint",
        "organization",
        ["org_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint(_DELIVERY_FK, "webhook_delivery", type_="foreignkey")
    op.create_foreign_key(
        _DELIVERY_FK,
        "webhook_delivery",
        "organization",
        ["org_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(_DELIVERY_FK, "webhook_delivery", type_="foreignkey")
    op.create_foreign_key(_DELIVERY_FK, "webhook_delivery", "organization", ["org_id"], ["id"])

    op.drop_constraint(_ENDPOINT_FK, "webhook_endpoint", type_="foreignkey")
    op.create_foreign_key(_ENDPOINT_FK, "webhook_endpoint", "organization", ["org_id"], ["id"])
