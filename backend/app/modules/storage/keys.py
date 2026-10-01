"""Where an uploaded object lives, before and after it is confirmed.

Review R1 (2026-09-29). An upload form (a presigned POST) stays valid for
its whole lifetime, ten minutes, and it can be used more than once. It used
to target the same key that downloads read. So an uploader could send
harmless bytes, confirm, wait for a clean scan, and then use the same form
again to replace the object -- with the database still saying "clean" and
every download serving the replacement. Hashing the scanned bytes bound the
*verdict* to them, and bound nothing that was served afterwards.

So the form now targets `incoming/<key>`, and confirm_upload copies that
object to `<key>` before anything checks it. Only the server writes `<key>`,
and every check -- size, content sniff, the virus scan -- runs on `<key>`,
so the bytes that are judged are the bytes that are served. Re-posting the
form afterwards writes `incoming/<key>` again and changes nothing anyone
reads.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.storage.backend import StorageBackend

INCOMING_PREFIX = "incoming/"


def upload_key(key: str) -> str:
    """Where the client's upload form is allowed to write, for canonical `key`."""
    return f"{INCOMING_PREFIX}{key}"


async def delete_every_copy(backend: StorageBackend, key: str) -> None:
    """Remove the object and whatever is still sitting at its upload key.

    The upload copy exists whenever a file was presigned and never
    confirmed (the bytes never left `incoming/`), and again whenever a
    confirmed file's form was re-posted before it expired. Deleting only
    `key` leaked those bytes where no quota counts them. Both deletes treat
    an absent object as success.
    """
    await backend.delete(key)
    await backend.delete(upload_key(key))
