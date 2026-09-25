"""One definition of "this object is safe to hand out", used by every
surface that can produce a download URL.

There are two such surfaces and they ask the question differently:
StorageService.get_download_url has the row in hand, and
app.modules.storage.avatars asks the database. So this module exposes the
same rule twice -- `require_clean_scan` for a loaded row,
`downloadable_where` for a SELECT -- rather than letting each caller write
its own predicate and drift.

The rule: an object is downloadable only once `status == "ready"` *and* a
scan has actually returned "clean". Confirm-time status is not enough.
`confirm_upload` sets "ready" and then enqueues the scan, so between those
two moments a file is confirmed, listed, and -- before this module existed
-- signable. That window is exactly when an uploaded object is most
interesting to whoever uploaded it.

Failing closed also makes the three not-clean outcomes mean something. A
scanner that is down leaves files "ready" with no verdict, a scanner that
errored leaves them "failed", and a verdict of malware leaves them
"infected" with the object already deleted. None of those should produce a
URL, and each should say something different to the caller, because "wait a
moment" and "this file is gone" are not the same instruction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from app.core.exceptions import ConflictError
from app.modules.storage.models import StoredFile

if TYPE_CHECKING:
    from sqlalchemy.sql.elements import ColumnElement

CLEAN = "clean"


def require_clean_scan(stored_file: StoredFile) -> None:
    """Raise unless `stored_file` has a clean verdict.

    ConflictError (409), not NotFoundError: the caller has already proved it
    may see this file, so hiding it now would only make a transient state
    look like a missing file -- and the client needs to tell "retry shortly"
    apart from "never".
    """
    if stored_file.status == "infected":
        raise ConflictError(
            "errors.file_infected",
            fallback="This file was found to contain malware and is no longer available.",
        )
    if stored_file.status == "failed":
        raise ConflictError(
            "errors.file_scan_failed",
            fallback="This file could not be scanned for malware, so it cannot be downloaded.",
        )
    if stored_file.status != "ready":
        raise ConflictError(
            "errors.file_not_ready",
            fallback="This file has not finished uploading yet.",
        )
    if stored_file.virus_scan_result != CLEAN:
        raise ConflictError(
            "errors.file_scan_pending",
            fallback="This file is still being scanned for malware. Try again shortly.",
        )


def downloadable_where() -> list[ColumnElement[bool]]:
    """`require_clean_scan` as SQL, for callers that filter rather than load."""
    model = cast("Any", StoredFile)
    return [model.status == "ready", model.virus_scan_result == CLEAN]
