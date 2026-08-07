"""
DB access for ProductSettings (T-263) -- the only layer allowed to build
queries (§0.1).

Deliberately not a `BaseRepository` subclass: that base is org-scoped and
soft-delete-aware (it appends `org_id == self.org_id` and
`deleted_at IS NULL` to everything it builds), and this table has neither
column. Inheriting it would mean overriding both behaviours away, which is a
longer way of writing the two queries below.
"""

from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models._mixins import utcnow

from .models import SINGLETON_ID, ProductSettings


class ProductSettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self) -> ProductSettings | None:
        """The singleton row, or None if the migration that seeds it hasn't
        run. Callers treat None as "every setting is at its default" rather
        than as an error -- a missing row must not be able to take the API
        down, and `read_settings()` in the service is what encodes that."""
        # cast, same as app.modules._template.repository: SQLModel annotates
        # `id: int`, so mypy --strict types the comparison as a plain `bool`
        # rather than as SQLAlchemy's ColumnElement[bool].
        model = cast("Any", ProductSettings)
        stmt = select(ProductSettings).where(model.id == SINGLETON_ID)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def replace(self, settings: dict[str, Any], actor_id: UUID | None) -> ProductSettings:
        """Store `settings` wholesale as the new value of the singleton.

        Upserts rather than assuming the row exists: the migration creates it,
        but a database restored from a backup taken before that migration --
        or a test that truncated the table -- would otherwise make every write
        fail with a confusing NoResultFound instead of just working.
        """
        row = await self.get()
        if row is None:
            row = ProductSettings(id=SINGLETON_ID)
            self._session.add(row)
        row.settings = settings
        row.updated_at = utcnow()
        row.updated_by = actor_id
        await self._session.flush()
        return row
