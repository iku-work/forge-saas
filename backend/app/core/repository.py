from datetime import datetime
from typing import Any, ClassVar, Protocol, cast
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models._mixins import utcnow

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20


class _OrgScopedRecord(Protocol):
    """Structural shape every BaseRepository model must have."""

    id: UUID
    org_id: UUID
    created_at: datetime
    deleted_at: datetime | None


class BaseRepository[T: _OrgScopedRecord]:
    """Generic org-scoped repository — see §0.2: org_id is sacred.

    SQLModel table classes aren't typed with SQLAlchemy's ``Mapped[]``, so
    mypy sees class-level column access (e.g. ``self.model.org_id``) as the
    plain annotated type rather than an ``InstrumentedAttribute``. Query
    construction below goes through a ``cast("Any", ...)`` view of the model
    for that reason; everything else keeps the real generic type ``T``.
    """

    model: ClassVar[type[T]]

    def __init__(self, session: AsyncSession, org_id: UUID) -> None:
        self.session = session
        self.org_id = org_id

    async def get(self, entity_id: UUID) -> T | None:
        m = cast("Any", self.model)
        stmt = select(self.model).where(
            m.id == entity_id,
            m.org_id == self.org_id,
            m.deleted_at.is_(None),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_or_raise(self, entity_id: UUID) -> T:
        obj = await self.get(entity_id)
        if obj is None:
            raise NotFoundError(
                "errors.resource_not_found",
                fallback=f"{self.model.__name__} not found",
                params={"resource": self.model.__name__},
            )
        return obj

    async def list_cursor(
        self, after: UUID | None = None, limit: int = DEFAULT_PAGE_SIZE
    ) -> tuple[list[T], UUID | None]:
        capped_limit = min(limit, MAX_PAGE_SIZE)
        m = cast("Any", self.model)
        stmt = (
            select(self.model)
            .where(m.org_id == self.org_id, m.deleted_at.is_(None))
            .order_by(m.created_at.desc(), m.id.desc())
            .limit(capped_limit + 1)
        )
        if after is not None:
            anchor = await self.get(after)
            if anchor is not None:
                stmt = stmt.where(tuple_(m.created_at, m.id) < (anchor.created_at, anchor.id))
        result = await self.session.execute(stmt)
        items = list(result.scalars().all())
        page = items[:capped_limit]
        # `page` guards against a non-positive limit: items can still be
        # non-empty (.limit(capped_limit + 1) == .limit(1) when capped_limit
        # is 0), but `page` is then empty -- page[-1] would raise IndexError.
        next_cursor = page[-1].id if page and len(items) > capped_limit else None
        return page, next_cursor

    async def create(self, **kwargs: Any) -> T:  # noqa: ANN401 — forwarded to arbitrary model ctors
        org_override = kwargs.get("org_id")
        if org_override is not None and org_override != self.org_id:
            raise ValueError("create() cannot set org_id to a different organization")
        kwargs["org_id"] = self.org_id
        obj = self.model(**kwargs)
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def update(self, entity_id: UUID, **kwargs: Any) -> T:  # noqa: ANN401 — see create()
        org_override = kwargs.get("org_id")
        if org_override is not None and org_override != self.org_id:
            raise ValueError("update() cannot change org_id to a different organization")
        obj = await self.get_or_raise(entity_id)
        for key, value in kwargs.items():
            setattr(obj, key, value)
        await self.session.flush()
        return obj

    async def soft_delete(self, entity_id: UUID) -> None:
        obj = await self.get_or_raise(entity_id)
        obj.deleted_at = utcnow()
        await self.session.flush()
