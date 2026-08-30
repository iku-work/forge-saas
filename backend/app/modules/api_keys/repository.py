"""
DB access for the api_keys module (T-310) -- the only layer allowed to build
queries (§0.1).

Two repositories rather than one, and the split is the whole point:

  * `ApiKeyRepository` is `BaseRepository`, org-scoped like every other
    repository in this codebase. The management endpoints go through it, so
    a key can only ever be listed, revoked or renamed by the org that owns
    it (§0.2).
  * `ApiKeyLookupRepository` is not org-scoped and cannot be, because the
    authentication path has a prefix in hand and no org yet -- the org *is*
    what the lookup resolves. It exposes exactly one method, it is used from
    exactly one place (app.modules.api_keys.service.authenticate), and it is
    separate so that "this query deliberately has no org filter" is a fact
    about a five-line class rather than a footnote on a method somebody adds
    a sibling to.

Imports are relative, like the scaffold's -- see app/modules/_template.
"""

from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.repository import BaseRepository

from .models import ApiKey


class ApiKeyRepository(BaseRepository[ApiKey]):
    """get / get_or_raise / list_cursor / create / update / soft_delete come
    from BaseRepository, already org-scoped and soft-delete-aware."""

    model = ApiKey

    async def count_live(self) -> int:
        """Keys that still authenticate or could be made to -- i.e. everything
        not soft-deleted, revoked included.

        Revoked keys count against the cap on purpose: they stay visible in
        the list (that is what `revoked_at` is for, distinct from
        `deleted_at`), so a cap that ignored them would let an org grow an
        unbounded page. Applying `org_id` and `deleted_at IS NULL` by hand is
        what any query written outside BaseRepository has to do -- forgetting
        the first is a cross-tenant leak, forgetting the second resurrects
        deleted rows.
        """
        m = cast("Any", ApiKey)
        stmt = (
            select(func.count())
            .select_from(ApiKey)
            .where(m.org_id == self.org_id, m.deleted_at.is_(None))
        )
        return (await self.session.execute(stmt)).scalar_one()


class ApiKeyLookupRepository:
    """The authentication path's single query. Not org-scoped -- see the
    module docstring."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_prefix(self, prefix: str) -> ApiKey | None:
        m = cast("Any", ApiKey)
        stmt = select(ApiKey).where(m.prefix == prefix, m.deleted_at.is_(None))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def touch_used(self, key_id: UUID, when: Any) -> None:  # noqa: ANN401 -- datetime, imported by the caller
        """Stamp `last_used_at`. Called at most once per key per minute; the
        throttle lives in the service, next to the Redis key that implements
        it, because it is a policy rather than a query."""
        row = await self.session.get(ApiKey, key_id)
        if row is not None:
            row.last_used_at = when
            await self.session.flush()
