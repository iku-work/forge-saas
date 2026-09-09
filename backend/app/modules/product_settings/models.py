"""
ProductSettings (T-263): the one row that holds every switch an operator can
flip *without a redeploy*.

Three layers of configuration exist in this codebase and they are deliberately
not the same thing:

  * `product.config.py` -- deploy-time. Which modules the product even ships
    with, its branding, its locales. Changing one is a code change and a
    deploy (see app.core.module_loader).
  * `OrgFeatureFlag` -- per-organization. One customer gets `advanced_reports`
    early (see app.modules.feature_flags).
  * `ProductSettings` -- *this* table. Global, runtime, operator-facing:
    maintenance mode, a banner, a kill switch. Flipped at 3am from the admin
    UI by someone who cannot wait for CI.

A single row, `id=1`, created by the migration rather than lazily on first
write: a lazily-created singleton means every reader has to handle "no row
yet", and two concurrent writers race to insert it. The primary key is an
`int` (not the UUID every other table in this codebase uses) precisely so the
constant `1` can be written down -- there is no second row to address.

Not org-scoped, and deliberately outside §0.2's "every user-data table carries
org_id" rule: this is not user data. It is the platform's own operating state,
readable by the maintenance middleware before any org is resolved, and no
tenant may read or write it (every route that touches it is behind
`require_superadmin`).
"""

from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy import CheckConstraint, Column, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models._mixins import utcnow

_TZ_DATETIME = DateTime(timezone=True)

# The id of the one row. Written down here rather than in each caller so
# nothing can address a second, invisible settings row by typo.
SINGLETON_ID = 1


class ProductSettings(SQLModel, table=True):
    __tablename__ = "product_settings"
    # The singleton rule, as the database enforces it. Declared here as well
    # as in a7e4c2f91b60 because a constraint the migration creates and the
    # model does not know about is drift: `alembic check` (and every
    # `revision --autogenerate`) proposes dropping it, which would quietly
    # remove the only thing stopping a second settings row from existing.
    # Alembic reflects CHECK constraints from 1.19 on, so this went unnoticed
    # until CI ran a newer version than any dev machine had.
    __table_args__: ClassVar[tuple[CheckConstraint, ...]] = (
        CheckConstraint(f"id = {SINGLETON_ID}", name="product_settings_singleton"),
    )

    id: int = Field(default=SINGLETON_ID, primary_key=True)
    # JSONB rather than a column per switch: the well-known keys
    # (product_settings.keys) are a *documented set*, not a schema. Adding one
    # must not need a migration -- that is the whole point of a table an
    # operator edits at 3am. Validation lives in keys.py, which is where a
    # reader looks for it.
    settings: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default="{}"),
    )
    updated_at: datetime = Field(default_factory=utcnow, sa_type=_TZ_DATETIME)  # type: ignore[call-overload]
    # Nullable and not a foreign_key, same reasoning as AuditEvent.actor_id:
    # the row survives the operator's own user row being deleted, and the
    # migration's seeded row has no author at all.
    updated_by: UUID | None = Field(default=None)

    # No EncryptedString column and so no RedactedReprMixin: nothing here is a
    # secret -- a banner and a handful of booleans. That is a constraint on
    # what may be added, not just a description of what is here today. A key
    # that *is* a secret (an API token for a status page, say) belongs in
    # Settings/vault, not in a JSONB blob the admin UI renders back to a
    # browser and this module logs a diff of.
