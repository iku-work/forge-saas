"""encrypt_user_full_name

Revision ID: d11c2b8e16c1
Revises: b3f7a1d95e42
Create Date: 2026-07-10 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
# T-219: new tip of the core trunk (see app/core/migrations.py's
# CORE_HEAD_REVISION, updated alongside this file) -- User.full_name is now
# backed by EncryptedString, whose own docstring requires the underlying
# column to be TEXT (Fernet tokens are always longer than the source
# plaintext, so a length-bounded VARCHAR would truncate/reject them).
revision: str = "d11c2b8e16c1"
down_revision: str | None = "b3f7a1d95e42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Widening VARCHAR -> TEXT is a metadata-only change in Postgres (no
    # rewrite, no USING clause needed) -- existing plaintext rows are left
    # exactly as they are and keep reading back correctly, since
    # EncryptedString.process_result_value passes through legacy plaintext
    # unchanged (see app/core/encrypted_type.py). New writes are encrypted
    # from here on; run rotate_encryption_keys_job(only=["user.full_name"])
    # (app/workers/jobs/encryption.py) once, out-of-band, to migrate the
    # pre-existing plaintext rows to ciphertext -- see
    # docs/runbooks/encryption-key-rotation.md.
    op.alter_column(
        "user",
        "full_name",
        existing_type=sa.String(),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    # §0.3: every migration must have a working downgrade() — this is not
    # optional boilerplate. CI runs upgrade -> downgrade -> upgrade and fails
    # the build if this function doesn't reverse upgrade() cleanly. Reversing
    # the column type doesn't un-encrypt any ciphertext already written under
    # the new EncryptedString-backed column -- those rows would need the
    # application-layer decrypt to read correctly regardless of the schema.
    op.alter_column(
        "user",
        "full_name",
        existing_type=sa.Text(),
        type_=sa.String(),
        existing_nullable=True,
    )
