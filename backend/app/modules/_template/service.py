"""
Business logic for the __MODULE_NAME__ module (T-250 scaffold).

The layer that owns *decisions*: what is allowed, what a limit is, what an
audit trail should say. It talks to the database only through
`ItemRepository` and knows nothing about HTTP -- no Request, no status codes,
no envelope. Failures leave as typed `DomainError`s (app.core.exceptions);
app.core.handlers is what turns each one into a status code and an error
envelope, so a service never has to.

Nothing here commits. The router owns the transaction boundary, so one
request stays one transaction even when it touches two services.
"""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import PlanLimitError, UnprocessableEntityError
from app.modules.audit.service import AuditService

from .models import Item
from .repository import ItemRepository
from .schemas import ItemCreateRequest, ItemUpdateRequest

# The plan-limit example. A real entitlement is read off the org's plan
# rather than hardcoded -- `org_sub.plan.features.get("max_items")`, with
# `org_sub` injected by app.modules.billing.dependencies.require_feature; see
# app.modules.storage.service._check_quota for that shape end to end. A plain
# constant is the version that works before this module is sold as anything:
# it keeps the failure mode (a typed PlanLimitError -> HTTP 402, not a
# 500 four months from now) without coupling a brand-new module to billing.
MAX_ACTIVE_ITEMS = 1000

RESOURCE_TYPE = "__MODULE_NAME___item"


class ItemService:
    def __init__(self, session: AsyncSession, org_id: UUID) -> None:
        self._session = session
        self._org_id = org_id
        self._repo = ItemRepository(session, org_id)
        self._audit = AuditService(session)

    async def create_item(self, user_id: UUID, payload: ItemCreateRequest) -> Item:
        await self._check_plan_limit()
        item = await self._repo.create(
            name=payload.name,
            status=payload.status,
            created_by=user_id,
        )
        await self._log("__MODULE_NAME__.item_created", user_id, item.id)
        return item

    async def list_items(self, after: UUID | None, limit: int) -> tuple[list[Item], UUID | None]:
        # Cursor pagination, never OFFSET (§0.3). BaseRepository caps `limit`
        # at MAX_PAGE_SIZE, so a caller cannot ask for the whole table.
        return await self._repo.list_cursor(after, limit)

    async def get_item(self, item_id: UUID) -> Item:
        # Raises NotFoundError for an id in another org exactly as it does for
        # one that never existed -- a 404 rather than a 403 is what keeps a
        # cross-tenant probe from confirming that the id is real.
        return await self._repo.get_or_raise(item_id)

    async def update_item(self, user_id: UUID, item_id: UUID, payload: ItemUpdateRequest) -> Item:
        changes: dict[str, Any] = payload.model_dump(exclude_unset=True)
        if not changes:
            raise UnprocessableEntityError(
                "errors.no_fields_to_update",
                fallback="No fields to update",
            )
        item = await self._repo.update(item_id, **changes)
        await self._log("__MODULE_NAME__.item_updated", user_id, item_id, diff=changes)
        return item

    async def delete_item(self, user_id: UUID, item_id: UUID) -> None:
        # Soft delete: the row stays, `deleted_at` is stamped, and every
        # BaseRepository read filters it out from here on.
        await self._repo.soft_delete(item_id)
        await self._log("__MODULE_NAME__.item_deleted", user_id, item_id)

    async def _check_plan_limit(self) -> None:
        if await self._repo.count_active() >= MAX_ACTIVE_ITEMS:
            raise PlanLimitError(
                "errors.plan_limit_reached",
                fallback=f"This organization is limited to {MAX_ACTIVE_ITEMS} active items",
                params={"limit": str(MAX_ACTIVE_ITEMS)},
            )

    async def _log(
        self,
        action: str,
        user_id: UUID,
        item_id: UUID,
        diff: dict[str, Any] | None = None,
    ) -> None:
        await self._audit.log(
            action=action,
            resource_type=RESOURCE_TYPE,
            resource_id=str(item_id),
            org_id=self._org_id,
            actor_id=user_id,
            diff=diff,
        )
