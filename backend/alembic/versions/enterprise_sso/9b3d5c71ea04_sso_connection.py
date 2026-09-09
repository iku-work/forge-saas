"""sso_connection

Revision ID: 9b3d5c71ea04
Revises: a7e4c2f91b60
Create Date: 2026-08-31 19:05:12.774301

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.encrypted_type import EncryptedString

# revision identifiers, used by Alembic.
# T-331: the first migration on the `enterprise_sso` branch, which branches
# off the core trunk's tip rather than chaining after another module -- see
# app/core/migrations.py for why each optional module gets its own branch
# label. The module ships off, so nothing here is applied unless
# product/config.py enables it.
revision: str = "9b3d5c71ea04"
down_revision: str | None = "a7e4c2f91b60"
branch_labels: str | Sequence[str] | None = "enterprise_sso"
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sso_connection",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider_label", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
        sa.Column("issuer", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=False),
        sa.Column("discovery_url", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=False),
        sa.Column("client_id", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        # §7's rule for a `*_secret` column. Encryption rather than hashing is
        # a requirement here, not a convention: the token exchange has to
        # present this value to the IdP, so a digest would make the column
        # useless. Same as webhook_endpoint.secret (T-320).
        sa.Column("client_secret", EncryptedString(), nullable=False),
        sa.Column("email_domain", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column("jit_provisioning", sa.Boolean(), nullable=False),
        # VARCHAR + CHECK rather than a native PG enum, exactly as membership
        # and invitation spell it -- see tenancy.models.member_role_column for
        # why (a native type outlives op.drop_table and breaks the next
        # upgrade).
        sa.Column(
            "default_role",
            sa.Enum("OWNER", "ADMIN", "MEMBER", name="memberrole", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("group_claim", sqlmodel.sql.sqltypes.AutoString(length=100), nullable=True),
        sa.Column("group_role_mappings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("enforced", sa.Boolean(), nullable=False),
        sa.Column("domain_hint_enabled", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        # CASCADE at the database level, as `api_key` does and for the same
        # reason: the product exposes no org-delete endpoint, so the only
        # thing that can remove an organization row is an operator at a psql
        # prompt -- and an SSO connection outliving its tenant is a login
        # route to an org that no longer exists.
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # One IdP per organization, enforced by the database rather than by
        # the service -- "we only ever create one" stops being true under a
        # retry.
        sa.UniqueConstraint("org_id", name="uq_sso_connection_org_id"),
    )
    # §7: the FK's own index, in the same migration as the FK.
    op.create_index(op.f("ix_sso_connection_org_id"), "sso_connection", ["org_id"], unique=False)
    # The login path's only lookup: resolve a connection from a typed email
    # domain.
    op.create_index(
        "ix_sso_connection_email_domain", "sso_connection", ["email_domain"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_sso_connection_email_domain", table_name="sso_connection")
    op.drop_index(op.f("ix_sso_connection_org_id"), table_name="sso_connection")
    op.drop_table("sso_connection")
