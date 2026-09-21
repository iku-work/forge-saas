"""When a session is still usable -- one definition, three call sites.

There are two independent expiry rules, and they used to be applied by
different amounts of the code:

  - the **absolute cap** (`expires_at`, anchored to the chain's original
    `auth_time` and never extended by rotation) -- 30 days by default;
  - the **idle cap** (`last_seen_at` within REFRESH_TOKEN_IDLE_TIMEOUT_MINUTES)
    -- 12 hours by default.

`AuthService.refresh()` enforced both. `SessionRepository.list_active_for_user`
and `count_active` enforced only the first. The consequence was visible on the
security page and measurable in production: of 15 sessions listed as "Devices
currently signed in to your account", 14 had passed the idle cap and could
never be refreshed again. They were dead, and the page was presenting them as
signed-in devices for up to thirty days.

That is worse than untidy. The entire purpose of that list is spotting a
session you do not recognise, and a real one would have been buried under
fourteen zombies.

So the rule lives here, in two forms that must agree:

  - `usable_session_clauses()` for queries;
  - `is_session_usable()` for a Session already loaded into memory, which is
    the shape `refresh()` needs.

A test asserts the two answer the same for the same row. Adding a third rule
means editing this file and nothing else.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from app.core.config import get_settings
from app.models._mixins import utcnow
from app.modules.auth.models import Session

if TYPE_CHECKING:
    from sqlalchemy.sql.elements import ColumnElement


def idle_cutoff(now: datetime | None = None) -> datetime:
    """The `last_seen_at` a session must be newer than to still be usable."""
    settings = get_settings()
    return (now or utcnow()) - timedelta(minutes=settings.REFRESH_TOKEN_IDLE_TIMEOUT_MINUTES)


def usable_session_clauses(now: datetime | None = None) -> list[ColumnElement[bool]]:
    """WHERE clauses selecting sessions a caller could still refresh.

    Returned as a list rather than one `and_(...)` so callers can add their own
    (`user_id == ...`) without unpacking anything.
    """
    moment = now or utcnow()
    m = cast("Any", Session)
    return [
        m.revoked_at.is_(None),
        m.expires_at > moment,
        m.last_seen_at > idle_cutoff(moment),
    ]


def is_session_usable(session: Session, now: datetime | None = None) -> bool:
    """The same rule, for a Session already in memory.

    `refresh()` works from the row it just looked up by token hash, so it
    cannot use the clauses above without a second round trip.
    """
    moment = now or utcnow()
    return (
        session.revoked_at is None
        and session.expires_at > moment
        and session.last_seen_at > idle_cutoff(moment)
    )
