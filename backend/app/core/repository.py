import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, ClassVar, Protocol, cast
from uuid import UUID

from sqlalchemy import Select, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import db
from app.core.exceptions import NotFoundError
from app.models._mixins import utcnow

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20

# T-246: `use_read_replica` may only appear on a method that reads. The check
# is deliberately two-sided -- the name must *start* like a read and must not
# contain a write verb anywhere -- so that `get_or_create` is rejected as
# loudly as `create` is. A method whose name says nothing useful either way
# (`sync_state`, `refresh_all`) is rejected too: renaming it is cheaper than
# debugging a write that silently went to a replica and vanished.
REPLICA_FLAG = "use_read_replica"
_READ_PREFIXES = frozenset(
    {"get", "list", "count", "exists", "find", "search", "fetch", "total", "has", "any"}
)
_WRITE_VERBS = frozenset(
    {
        "create",
        "update",
        "delete",
        "insert",
        "upsert",
        "set",
        "add",
        "remove",
        "mark",
        "purge",
        "prune",
        "restore",
        "save",
        "assign",
        "revoke",
        "increment",
        "decrement",
        "touch",
        "archive",
        "rotate",
        "invalidate",
        "expire",
        "write",
    }
)


def _may_carry_replica_flag(method_name: str) -> bool:
    tokens = method_name.split("_")
    return tokens[0] in _READ_PREFIXES and not (set(tokens) & _WRITE_VERBS)


def check_replica_flags(cls: type) -> None:
    """Reject `use_read_replica` on anything that isn't a read. Raises TypeError.

    Runs at class-definition time for every ReplicaReadMixin subclass, and is
    applied to the hand-rolled repositories (which don't inherit the mixin) by
    the sweep in tests/test_read_replica.py -- so the flag cannot reach a
    mutating method anywhere in the codebase, inherited or not.

    Underscore-prefixed methods are exempt: they are the routing plumbing
    itself (_fetch_all / _fetch_one below), not a repository's public API.
    """
    for name, member in inspect.getmembers(cls, callable):
        if name.startswith("_"):
            continue
        try:
            parameters = inspect.signature(member).parameters
        except (TypeError, ValueError):  # pragma: no cover -- C-level callables
            continue
        if REPLICA_FLAG in parameters and not _may_carry_replica_flag(name):
            raise TypeError(
                f"{cls.__name__}.{name}() takes {REPLICA_FLAG}, but its name says it is not "
                f"a read. Read-replica routing is only for methods that read: name it with "
                f"one of {sorted(_READ_PREFIXES)} and no write verb, or drop the flag. "
                f"See docs/adr/0003-read-replica.md."
            )


class ReplicaReadMixin:
    """Opt-in, per-method read routing (T-246).

    A repository method that can tolerate replication lag takes
    `use_read_replica: bool = False` and runs its statement through
    _fetch_all/_fetch_one. With no replica configured -- the default, and every
    deployment until someone provisions one -- that resolves to the caller's own
    session and this whole mixin is a no-op.

    Two things are deliberately *not* possible here: routing by HTTP verb (the
    caller names the method, the method names its own tolerance) and routing a
    statement that touches auth state (app.core.db.PRIMARY_ONLY_TABLES).
    """

    session: AsyncSession

    def __init_subclass__(cls, **kwargs: Any) -> None:  # noqa: ANN401 -- passthrough
        super().__init_subclass__(**kwargs)
        check_replica_flags(cls)

    @asynccontextmanager
    async def _reading(
        self, statement: Select[Any], *, use_read_replica: bool
    ) -> AsyncIterator[AsyncSession]:
        if use_read_replica and db.should_route_to_replica(statement):
            async with db.read_session_scope() as replica_session:
                yield replica_session
            return
        yield self.session

    async def _fetch_all[R](self, statement: Select[Any], *, use_read_replica: bool) -> list[R]:
        async with self._reading(statement, use_read_replica=use_read_replica) as session:
            rows: list[R] = list((await session.execute(statement)).scalars().all())
            if session is not self.session:
                # Detach before the replica session closes: the rows keep the
                # column values they loaded, and nothing later mistakes them
                # for rows the caller's unit of work is tracking.
                session.expunge_all()
            return rows

    async def _fetch_one[R](self, statement: Select[Any], *, use_read_replica: bool) -> R | None:
        async with self._reading(statement, use_read_replica=use_read_replica) as session:
            # scalar_one_or_none(), not rows[0]: a statement that unexpectedly
            # matches two rows must still raise, exactly as it does unrouted.
            row: R | None = (await session.execute(statement)).scalar_one_or_none()
            if session is not self.session:
                session.expunge_all()
            return row


class _OrgScopedRecord(Protocol):
    """Structural shape every BaseRepository model must have."""

    id: UUID
    org_id: UUID
    created_at: datetime
    deleted_at: datetime | None


class BaseRepository[T: _OrgScopedRecord](ReplicaReadMixin):
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

    async def get(self, entity_id: UUID, *, use_read_replica: bool = False) -> T | None:
        m = cast("Any", self.model)
        stmt = select(self.model).where(
            m.id == entity_id,
            m.org_id == self.org_id,
            m.deleted_at.is_(None),
        )
        return await self._fetch_one(stmt, use_read_replica=use_read_replica)

    async def get_or_raise(self, entity_id: UUID) -> T:
        # No use_read_replica here, by design: update() and soft_delete() load
        # the row they are about to mutate through this method, and a row read
        # from a replica belongs to a session that is already closed.
        obj = await self.get(entity_id)
        if obj is None:
            raise NotFoundError(
                "errors.resource_not_found",
                fallback=f"{self.model.__name__} not found",
                params={"resource": self.model.__name__},
            )
        return obj

    async def list_cursor(
        self,
        after: UUID | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        *,
        use_read_replica: bool = False,
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
            # The anchor read follows the page it anchors: resolving the cursor
            # on the primary while paging from the replica would compare a
            # (created_at, id) pair against rows that may not exist there yet.
            anchor = await self.get(after, use_read_replica=use_read_replica)
            if anchor is not None:
                stmt = stmt.where(tuple_(m.created_at, m.id) < (anchor.created_at, anchor.id))
        items: list[T] = await self._fetch_all(stmt, use_read_replica=use_read_replica)
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
