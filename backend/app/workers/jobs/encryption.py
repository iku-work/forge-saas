"""
ARQ job: zero-downtime re-encryption of EncryptedString columns after a key
rotation. See app.core.encrypted_type for the column type and the full
rotation workflow (set ENCRYPTION_KEY_OLD to the previous key, deploy, run
this job per encrypted column, then drop ENCRYPTION_KEY_OLD once every row
has been migrated to the new primary key).
"""

import logging
import re
from typing import TYPE_CHECKING, Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import async_session_factory
from app.core.encrypted_type import get_shared_fernet

if TYPE_CHECKING:
    from uuid import UUID

logger = logging.getLogger(__name__)

_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _validated_identifier(name: str, kind: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(name):
        raise ValueError(f"Invalid {kind} name for reencrypt_column_job: {name!r}")
    return name


def _reencrypted_value(raw_value: str | None, primary: Fernet, all_keys: MultiFernet) -> str | None:
    """Returns new primary-key ciphertext for `raw_value`, or None if no change is needed."""
    if raw_value is None:
        return None

    try:
        primary.decrypt(raw_value.encode("ascii"))
    except (InvalidToken, UnicodeEncodeError):
        pass
    else:
        return None  # already encrypted with the primary key — idempotent skip

    try:
        plaintext_bytes = all_keys.decrypt(raw_value.encode("ascii"))
    except (InvalidToken, UnicodeEncodeError):
        # Not decryptable by any known key — either it was never encrypted
        # (legacy plaintext, the pre-encryption migration path) or it's
        # genuinely corrupt. Either way, the safe move is to encrypt the raw
        # bytes as-is: a legacy row gets migrated; corrupt ciphertext is
        # preserved (re-encrypting garbage is no worse than leaving it).
        plaintext_bytes = raw_value.encode("utf-8")

    return primary.encrypt(plaintext_bytes).decode("ascii")


async def reencrypt_column_job(
    ctx: dict[str, Any],  # noqa: ARG001 -- ARQ calling convention; wired up in T-034
    *,
    table: str,
    column: str,
    batch_size: int = 500,
) -> None:
    """
    Re-encrypts every row in `table.column` that isn't already encrypted
    with the current primary ENCRYPTION_KEY. Assumes a UUID primary key
    column named `id` (the UUIDPKMixin convention used throughout app.models).

    Idempotent and restartable: a row already on the primary key is
    recognised as current (see _reencrypted_value) and skipped, so re-running
    the job after a partial or failed run is always safe.
    """
    validated_table = _validated_identifier(table, "table")
    validated_column = _validated_identifier(column, "column")

    primary_fernet = Fernet(get_settings().ENCRYPTION_KEY.encode("ascii"))
    all_keys_fernet = get_shared_fernet()

    # table/column are validated against _IDENTIFIER_RE above (never raw user
    # input — callers are internal ops/jobs code), so identifier interpolation
    # here can't introduce SQL injection; row values are still bound params.
    select_first = text(  # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text  # noqa: E501
        f'SELECT id, "{validated_column}" FROM "{validated_table}" ORDER BY id LIMIT :limit'  # noqa: S608  # nosec B608
    )
    select_after = text(  # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text  # noqa: E501
        f'SELECT id, "{validated_column}" FROM "{validated_table}" '  # noqa: S608  # nosec B608
        "WHERE id > :after_id ORDER BY id LIMIT :limit"
    )
    update_row = text(  # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text  # noqa: E501
        f'UPDATE "{validated_table}" SET "{validated_column}" = :value WHERE id = :id'  # noqa: S608  # nosec B608
    )

    last_id: UUID | None = None
    total_rotated = 0

    async with async_session_factory() as session:
        while True:
            if last_id is None:
                rows = (await session.execute(select_first, {"limit": batch_size})).all()
            else:
                rows = (
                    await session.execute(select_after, {"after_id": last_id, "limit": batch_size})
                ).all()

            if not rows:
                break

            rotated_in_batch = 0
            for row_id, raw_value in rows:
                last_id = row_id
                new_ciphertext = _reencrypted_value(raw_value, primary_fernet, all_keys_fernet)
                if new_ciphertext is not None:
                    await session.execute(update_row, {"value": new_ciphertext, "id": row_id})
                    rotated_in_batch += 1

            await session.commit()
            total_rotated += rotated_in_batch
            logger.info(
                "reencrypt_column_job: batch complete (table=%s, column=%s, "
                "rotated_in_batch=%d, total_rotated=%d)",
                validated_table,
                validated_column,
                rotated_in_batch,
                total_rotated,
            )

    logger.info(
        "reencrypt_column_job: done (table=%s, column=%s, total_rotated=%d)",
        validated_table,
        validated_column,
        total_rotated,
    )
