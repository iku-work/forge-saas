"""A user's own avatar: not an organisation's file, and published where anyone
can read it.

An avatar belongs to the person and follows them between organisations, so it
is not a StoredFile: it uses no organisation's quota and needs no permission in
one. Until 2026-11 it was an org file, and a member who could not upload files
in their organisation could not change their own picture.

The bytes go:

    POST /auth/me/avatar/upload-form   `presign_upload`: a form that can write
                                       one object, incoming/avatars/<user>/<random>,
                                       of up to 5 MB. The browser posts the file
                                       straight to storage, like every upload.
    POST /auth/me/avatar               names that key; `is_own_upload` checks it
                                       is the caller's, and the job is queued
    process_avatar         scanned, then `reencode`d -- only decoded pixels
    (scanner worker)       survive, so EXIF, GPS and anything else riding in
                           the file are gone -- and `publish`ed at
                           public/avatars/<random>.webp, which needs no
                           signature to read (scripts/minio-init.sh)

Decoding happens in the scanner worker, never in the API process: a decoder
bug or a decompression bomb costs that container, under its own memory limit.
An upload nobody processes expires with the rest of incoming/ after a day.
"""

from __future__ import annotations

import io
import re
from typing import TYPE_CHECKING
from uuid import uuid4

from PIL import Image, ImageOps

from app.modules.auth.models import AVATAR_KEY_PREFIX
from app.modules.storage.backend import get_storage_backend
from app.modules.storage.keys import INCOMING_PREFIX

if TYPE_CHECKING:
    from uuid import UUID

    from app.modules.storage.backend import PresignedUpload

MAX_AVATAR_BYTES = 5_000_000
AVATAR_SIZE = 512
_INCOMING_AVATARS = f"{INCOMING_PREFIX}avatars/"
# The largest image of each format, read from the header before a pixel is
# decoded, and sized to the scanner worker's 384 MB (measured 2026-11-10 with
# Pillow 13). A JPEG decodes at a reduced scale, so 50 MP costs ~20 MB; a PNG
# or WebP decodes in full, and at 25 MP a WebP peaked at 380 MB. 12 MP is
# about 180 MB, and a 12 MP phone photo is a JPEG anyway.
_MAX_PIXELS = {"JPEG": 50_000_000, "PNG": 12_000_000, "WEBP": 12_000_000}
# The key is new for every upload, so a cached copy is never stale.
_CACHE_CONTROL = "public, max-age=31536000, immutable"


class InvalidAvatarError(Exception):
    """Not an image this accepts. The message is for the log, not the user."""


async def presign_upload(user_id: UUID, content_type: str) -> PresignedUpload:
    """A form for one new object under the user's own incoming prefix.

    The declared type is only what the form insists on; `reencode` decides
    what the bytes are.
    """
    key = f"{_INCOMING_AVATARS}{user_id}/{uuid4()}"
    return await get_storage_backend().presign_upload(key, content_type, max_bytes=MAX_AVATAR_BYTES)


async def is_own_upload(user_id: UUID, key: str) -> bool:
    """Whether `key` is an upload this user's form made, and it arrived.

    The prefix is the proof: only a form from `presign_upload` for this user
    can write under it.
    """
    pattern = rf"{re.escape(_INCOMING_AVATARS)}{user_id}/[0-9a-f-]{{36}}"
    return re.fullmatch(pattern, key) is not None and await get_storage_backend().exists(key)


def reencode(raw: bytes) -> bytes:
    """A 512 px square WebP of the image's pixels, and nothing else from the file.

    Raises InvalidAvatarError for anything that is not a JPEG, PNG or WebP
    Pillow can decode, or is too large to decode safely.
    """
    try:
        with Image.open(io.BytesIO(raw)) as image:
            limit = _MAX_PIXELS.get(image.format or "")
            if limit is None:
                raise InvalidAvatarError(f"format {image.format}")
            if image.width * image.height > limit:
                raise InvalidAvatarError(f"{image.format} of {image.width}x{image.height} pixels")
            # Shrink before anything else copies it: transposing, converting
            # and cropping each copy the image, and at full size four copies
            # of a 25 MP PNG passed the scanner worker's 384 MB. `draft` makes
            # a JPEG decode at a reduced scale to begin with.
            image.draft("RGB", (AVATAR_SIZE * 2, AVATAR_SIZE * 2))
            factor = min(image.size) // (AVATAR_SIZE * 2)
            small = image.reduce(factor) if factor > 1 else image
            upright = ImageOps.exif_transpose(small)
            transparent = "A" in upright.getbands() or "transparency" in upright.info
            square = ImageOps.fit(
                upright.convert("RGBA" if transparent else "RGB"),
                (AVATAR_SIZE, AVATAR_SIZE),
                Image.Resampling.LANCZOS,
            )
    # A broken file is an UnidentifiedImageError (an OSError) or a ValueError.
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise InvalidAvatarError(str(exc)) from exc
    out = io.BytesIO()
    square.save(out, "WEBP", quality=85)
    return out.getvalue()


async def publish(webp: bytes) -> str:
    key = f"{AVATAR_KEY_PREFIX}{uuid4()}.webp"
    await get_storage_backend().put(key, webp, "image/webp", cache_control=_CACHE_CONTROL)
    return key


async def delete_avatar_objects(*keys: str | None) -> None:
    """Delete published avatars and staged uploads. Any other key is left
    alone: an avatar from before 2026-11 is an org's StoredFile."""
    backend = get_storage_backend()
    for key in keys:
        if key and key.startswith((AVATAR_KEY_PREFIX, _INCOMING_AVATARS)):
            await backend.delete(key)
