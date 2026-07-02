"""T-110: StoredFile, the single table tracking all uploaded file metadata."""

from typing import ClassVar
from uuid import UUID

from sqlalchemy import Index
from sqlmodel import Field, SQLModel

from app.models._mixins import OrgScopedMixin, SoftDeleteMixin, TimestampMixin, UUIDPKMixin


class StoredFile(
    UUIDPKMixin, TimestampMixin, SoftDeleteMixin, OrgScopedMixin, SQLModel, table=True
):
    __tablename__ = "stored_file"
    __table_args__: ClassVar[tuple[Index, ...]] = (
        # Cursor pagination over an org's files, mirrors BaseRepository.list_cursor's
        # ORDER BY created_at DESC, id DESC (see notification's equivalent index).
        Index(
            "ix_stored_file_org_id_status_created_at_id",
            "org_id",
            "status",
            "created_at",
            "id",
        ),
        # "files uploaded by me" lookups.
        Index("ix_stored_file_org_id_uploaded_by", "org_id", "uploaded_by"),
    )

    uploaded_by: UUID = Field(foreign_key="user.id", index=True)
    key: str = Field(unique=True, index=True)  # storage object key (org_id-prefixed)
    filename: str
    content_type: str = Field(index=True)
    size_bytes: int
    checksum_sha256: str | None = Field(default=None)  # set after confirm (T-112)
    # "pending" | "ready" | "infected" | "failed" | "deleted"
    status: str = Field(default="pending")
    virus_scan_result: str | None = Field(default=None)
    # logical bucket for plan-quota counting: "default" | "avatars" | "exports" | ...
    bucket: str = Field(default="default")
