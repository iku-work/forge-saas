"""One definition of what counts against an organization's storage quota.

Shared by the repository (which sums the bytes), the service (which checks
the sum before signing) and the scheduler job (which cleans up what the sum
stops counting), because those three disagreeing is precisely the bug this
module exists to close. Same shape, and the same reason, as `scan_gate`.

**Pending uploads are reservations.** `total_bytes_for_org` used to count
only `ready` rows, which meant a presign checked the quota against bytes
that were already there and ignored every upload it had itself authorised
moments earlier. Ten presigns for 1 GB each against 1 GB of free space all
passed, and then all ten confirmed: the quota was a suggestion under any
concurrency at all, including the entirely ordinary concurrency of a
dropzone uploading several files at once (`FileUploader` runs three).

So a `pending` row counts too -- but only while its reservation is live,
or an abandoned presign would hold space forever against a client that
closed the tab.

**Why the window is an hour and not the ten-minute signing TTL.** A
presigned URL's expiry bounds when the upload may *start*, not when it must
finish: a PUT begun at 9:59 can still be streaming an hour later, and S3
will accept it. Expiring the reservation at ten minutes would therefore
uncount bytes that are actively arriving. An hour is generous enough for a
large file on a slow line and short enough that abandoned reservations
clear within one sweep.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from app.core.config import get_settings
from app.core.exceptions import UnprocessableEntityError
from app.models._mixins import utcnow
from app.modules.storage.models import StoredFile

if TYPE_CHECKING:
    from sqlalchemy.sql.elements import ColumnElement

UPLOAD_RESERVATION_WINDOW = timedelta(hours=1)


def reservation_cutoff(now: datetime | None = None) -> datetime:
    """The moment before which a `pending` row's reservation has lapsed."""
    return (now or utcnow()) - UPLOAD_RESERVATION_WINDOW


def counts_toward_quota(now: datetime | None = None) -> ColumnElement[bool]:
    """The rows whose `size_bytes` occupy an org's quota right now.

    A single expression rather than a list of clauses, because the two
    halves are an OR: uploads that finished, plus uploads that are still
    allowed to.
    """
    model = cast("Any", StoredFile)
    finished: ColumnElement[bool] = model.status == "ready"
    reserved: ColumnElement[bool] = (model.status == "pending") & (
        model.created_at > reservation_cutoff(now)
    )
    return cast("ColumnElement[bool]", finished | reserved)


def check_absolute_ceiling(size_bytes: int) -> None:
    """Enforce `MAX_UPLOAD_BYTES`, whatever the billing module is doing.

    Read at call time, never bound at import: settings come from the
    environment the process was actually started with, and a module-level
    `get_settings()` would capture whichever `.env` was on disk when the
    module first imported.
    """
    ceiling = get_settings().MAX_UPLOAD_BYTES
    if size_bytes > ceiling:
        raise UnprocessableEntityError(
            "errors.file_exceeds_maximum",
            fallback=f"File exceeds the {ceiling // (1024 * 1024)}MB maximum upload size",
            params={"max_upload_mb": str(ceiling // (1024 * 1024))},
        )
