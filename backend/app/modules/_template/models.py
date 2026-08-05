"""
Tables for the __MODULE_NAME__ module (T-250 scaffold).

`Item` is the one example entity every generated module starts from. Rename
it to `__ModelName__` -- the thing this module is actually about -- once you
know what that is; README.md's checklist lists everything that renames with
it.

The four mixins, in the order every org-scoped table in this codebase applies
them (compare app.modules.storage.models.StoredFile): UUID primary key,
created_at/updated_at, soft delete, and org_id. `OrgScopedMixin` is what makes
BaseRepository's org filter possible -- §0.2: org_id is sacred, and a table
without it can never be safely served by an org-scoped endpoint.
"""

from typing import ClassVar
from uuid import UUID

from sqlalchemy import Index
from sqlmodel import Field, SQLModel

from app.models._mixins import OrgScopedMixin, SoftDeleteMixin, TimestampMixin, UUIDPKMixin


class Item(UUIDPKMixin, TimestampMixin, SoftDeleteMixin, OrgScopedMixin, SQLModel, table=True):
    # The module placeholder is part of the table name on purpose: two
    # modules generated before either entity is renamed would otherwise both
    # claim `item` and collide on the shared SQLModel.metadata at import time.
    __tablename__ = "__MODULE_NAME___item"
    __table_args__: ClassVar[tuple[Index, ...]] = (
        # Covers the page BaseRepository.list_cursor emits (ORDER BY
        # created_at DESC, id DESC) with the `status` filter in front of it --
        # same shape as ix_stored_file_org_id_status_created_at_id. Keyset
        # pagination without this index is a sort of the whole org's rows
        # (§0.3).
        Index(
            "ix___MODULE_NAME___item_org_id_status_created_at_id",
            "org_id",
            "status",
            "created_at",
            "id",
        ),
    )

    name: str = Field(max_length=200)
    # The one indexed filter column. Every list endpoint filters on something,
    # and an unindexed filter is a sequential scan waiting for the first
    # customer with 100k rows.
    status: str = Field(default="active", index=True)
    created_by: UUID = Field(foreign_key="user.id", index=True)
