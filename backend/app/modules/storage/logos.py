"""Resolving and serving an organization's uploaded logo.

The org-scoped counterpart of `avatars.py`. A logo belongs to the
organization, so it is uploaded through that org's storage like any file,
into the `logos` bucket, and every query here filters on the org (§0.2): a
logo can only be chosen from, and served out of, the organization it was
uploaded to.

The bucket stays private. What reaches the browser is a short-lived presigned
URL, and only once the file's malware scan is clean.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import or_, select

from app.core.exceptions import NotFoundError
from app.modules.storage.backend import get_storage_backend
from app.modules.storage.models import StoredFile
from app.modules.storage.scan_gate import CLEAN, require_clean_scan

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

LOGO_BUCKET = "logos"

LOGO_URL_EXPIRES_IN_SECONDS = 300


async def resolve_org_logo_key(session: AsyncSession, *, org_id: UUID, file_id: UUID) -> str | None:
    """The object key of `file_id`, if it may become `org_id`'s logo.

    None when the file is not this org's, not in the logos bucket, not
    finished uploading, or failed its scan -- one answer, so another org's
    file ids reveal nothing. A scan still pending is accepted, as for
    avatars: choosing the file is valid before the verdict, serving it is not
    (see `presign_org_logo`).
    """
    model = cast("Any", StoredFile)
    stmt = select(StoredFile).where(
        model.id == file_id,
        model.org_id == org_id,
        model.bucket == LOGO_BUCKET,
        model.status == "ready",
        or_(model.virus_scan_result.is_(None), model.virus_scan_result == CLEAN),
        model.deleted_at.is_(None),
    )
    stored_file = (await session.execute(stmt)).scalar_one_or_none()
    return stored_file.key if stored_file is not None else None


async def presign_org_logo(session: AsyncSession, *, org_id: UUID, key: str) -> str:
    """A short-lived URL for the logo stored at `key` -- once it is clean.

    409 while the scan is pending or after it failed; 404 if the file is gone.
    """
    model = cast("Any", StoredFile)
    stmt = select(StoredFile).where(
        model.key == key, model.org_id == org_id, model.deleted_at.is_(None)
    )
    stored_file = (await session.execute(stmt)).scalar_one_or_none()
    if stored_file is None:
        raise NotFoundError("errors.logo_not_found", fallback="No uploaded logo.")
    require_clean_scan(stored_file)
    return await get_storage_backend().presign_download(key, LOGO_URL_EXPIRES_IN_SECONDS)
