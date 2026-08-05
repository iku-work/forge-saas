"""
DB access for the __MODULE_NAME__ module (T-250 scaffold) -- the only layer
allowed to build queries (§0.1).

Imports inside this module are relative (`from .models import Item`) rather
than absolute: the package is `app.modules._template` here and
`app.modules.<your name>` after `make new-module`, and a relative import is
the one form that keeps working across that rename without the generator
having to rewrite it.
"""

from typing import Any, cast

from sqlalchemy import func, select

from app.core.repository import BaseRepository

from .models import Item

ACTIVE_STATUS = "active"


class ItemRepository(BaseRepository[Item]):
    """get / get_or_raise / list_cursor / create / update / soft_delete come
    from BaseRepository, already org-scoped and soft-delete-aware. Add methods
    here only for queries it cannot express."""

    model = Item

    async def count_active(self) -> int:
        # The pattern every hand-written query in this codebase repeats, and
        # the reason to read this method before writing your own: BaseRepository
        # applies `org_id == self.org_id` (§0.2) and `deleted_at IS NULL`
        # to everything it builds -- a query you add by hand has to apply both
        # itself. Forgetting the first is a cross-tenant data leak; forgetting
        # the second resurrects deleted rows.
        m = cast("Any", Item)
        stmt = (
            select(func.count())
            .select_from(Item)
            .where(
                m.org_id == self.org_id,
                m.status == ACTIVE_STATUS,
                m.deleted_at.is_(None),
            )
        )
        return (await self.session.execute(stmt)).scalar_one()
