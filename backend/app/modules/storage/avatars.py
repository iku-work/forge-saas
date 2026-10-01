"""Resolving a user's own avatar, which is the one file that is not org-scoped.

Everything else in this module is scoped to an organisation:
StoredFileRepository takes an org_id and every query filters on it, because a
document belongs to the org that paid for the storage. An avatar does not. It
belongs to the *person*, it follows them between organisations, and the
profile page that shows it has no org in view at all.

So these two functions look a file up by `uploaded_by` instead of by `org_id`.
That is the whole reason they exist rather than living on StorageService,
which cannot be constructed without an org -- and it is why `uploaded_by` is
checked on the way in rather than assumed: without it a user could point
`avatar_object_key` at any file id in the system and have GET
/auth/me/avatar presign it for them.

The bucket stays private throughout. What reaches the browser is a
short-lived presigned URL, never a public object.
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

AVATAR_BUCKET = "avatars"

# Shorter than the storage module's own download links. An avatar URL is
# fetched when the page renders and used immediately; a long-lived signed URL
# to a personal photo is a link worth not leaving lying around in a browser
# history or a proxy log.
AVATAR_URL_EXPIRES_IN_SECONDS = 300


async def resolve_own_avatar_key(
    session: AsyncSession, *, user_id: UUID, file_id: UUID
) -> str | None:
    """The object key of `file_id`, if it may become this user's avatar.

    None -- never an exception -- when the file does not exist, was uploaded
    by somebody else, is not in the avatars bucket, or has been judged and
    failed (infected, or unscannable). The caller turns that into a 422;
    there is nothing here worth distinguishing for the client, and saying
    which one failed would confirm the existence of another user's file.

    A file whose scan has **not finished yet** is accepted (plan D4, review
    R7). Choosing it as your avatar is a statement of intent that is valid
    before the verdict; *serving* it is not, and that gate lives in
    `presign_own_avatar`, which every read goes through. Requiring the
    verdict here meant the profile page -- which sets the avatar the moment
    the upload is confirmed, before any scanner could have finished -- failed
    nearly every first upload with a generic error.
    """
    model = cast("Any", StoredFile)
    stmt = select(StoredFile).where(
        model.id == file_id,
        model.uploaded_by == user_id,
        model.bucket == AVATAR_BUCKET,
        model.status == "ready",
        or_(model.virus_scan_result.is_(None), model.virus_scan_result == CLEAN),
        model.deleted_at.is_(None),
    )
    stored_file = (await session.execute(stmt)).scalar_one_or_none()
    return stored_file.key if stored_file is not None else None


async def presign_own_avatar(session: AsyncSession, key: str) -> str:
    """A short-lived URL for the avatar stored at `key` -- once it is clean.

    The scan gate for avatars, applied where the bytes are handed out rather
    than where the choice is made (see `resolve_own_avatar_key`). An avatar
    has no org, so it never passes through StorageService, and this is the
    only thing between the stored object and a browser. 409 with the scan
    gate's own message while the scan is pending or after it failed; 404 if
    the file is gone.
    """
    model = cast("Any", StoredFile)
    stmt = select(StoredFile).where(model.key == key, model.deleted_at.is_(None))
    stored_file = (await session.execute(stmt)).scalar_one_or_none()
    if stored_file is None:
        raise NotFoundError("errors.avatar_not_found", fallback="No uploaded avatar.")
    require_clean_scan(stored_file)
    return await presign_avatar(key)


async def presign_avatar(key: str) -> str:
    """A short-lived URL the browser can put in an <img src>.

    Presigned rather than public: the avatars bucket is private like every
    other, so this is the only way the image reaches a page without opening
    the bucket up.
    """
    return await get_storage_backend().presign_download(key, AVATAR_URL_EXPIRES_IN_SECONDS)
