"""
HTTP layer for the __MODULE_NAME__ module (T-250 scaffold): CRUD under
/api/v1/__MODULE_NAME__/.

Five things every route in this codebase does, all visible below:

  - `Envelope`/`Meta` on every response (§0.5). Errors get the same envelope
    without any work here -- app.core.handlers renders every DomainError the
    service raises.
  - `require_permission(...)` on every route (§0.7). It resolves X-Org-ID,
    proves the caller is a member of that org, checks the RBAC matrix, and
    hands back the `Membership` -- which is also where `org_id` and the actor's
    `user_id` come from, so no route needs to accept either from the client.
  - Cursor pagination on the list route (§0.3): `after` + `limit`, and the
    next cursor travels in `meta.next_cursor`.
  - `@api_limit` on every route (§0.9).
  - `await session.commit()` after a write, and only here: the router owns
    the transaction boundary, the service never commits.
"""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.dependencies import require_permission
from app.core.envelope import Envelope, Meta
from app.middleware.rate_limit import api_limit
from app.modules.auth.permissions import Permission
from app.modules.tenancy.models import Membership

from .schemas import ItemCreateRequest, ItemPublic, ItemUpdateRequest
from .service import ItemService

# Checklist item 1 in README.md: these point at existing permissions so a
# freshly generated module is gated from its first request, before you touch
# the shared RBAC matrix. Replace them with your own
# `__MODULE_NAME__:read` / `__MODULE_NAME__:manage` entries in
# app.modules.auth.permissions (Permission + ROLE_PERMISSIONS) -- both are
# code, so it is a deploy, not a migration.
READ_PERMISSION = Permission.MEMBERS_READ
MANAGE_PERMISSION = Permission.SETTINGS_MANAGE

router = APIRouter(prefix="/__MODULE_NAME__", tags=["__MODULE_NAME__"])


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid4())


@router.post("")
@api_limit
async def create_item(
    request: Request,
    body: ItemCreateRequest,
    membership: Annotated[Membership, Depends(require_permission(MANAGE_PERMISSION))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[ItemPublic]:
    item = await ItemService(session, membership.org_id).create_item(membership.user_id, body)
    await session.commit()
    return Envelope(
        data=ItemPublic.model_validate(item),
        meta=Meta(request_id=_request_id(request)),
    )


@router.get("")
@api_limit
async def list_items(
    request: Request,
    membership: Annotated[Membership, Depends(require_permission(READ_PERMISSION))],
    session: Annotated[AsyncSession, Depends(get_session)],
    after: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Envelope[list[ItemPublic]]:
    items, next_cursor = await ItemService(session, membership.org_id).list_items(after, limit)
    return Envelope(
        data=[ItemPublic.model_validate(item) for item in items],
        meta=Meta(
            request_id=_request_id(request),
            next_cursor=str(next_cursor) if next_cursor is not None else None,
        ),
    )


@router.get("/{item_id}")
@api_limit
async def get_item(
    request: Request,
    item_id: UUID,
    membership: Annotated[Membership, Depends(require_permission(READ_PERMISSION))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[ItemPublic]:
    item = await ItemService(session, membership.org_id).get_item(item_id)
    return Envelope(
        data=ItemPublic.model_validate(item),
        meta=Meta(request_id=_request_id(request)),
    )


@router.patch("/{item_id}")
@api_limit
async def update_item(
    request: Request,
    item_id: UUID,
    body: ItemUpdateRequest,
    membership: Annotated[Membership, Depends(require_permission(MANAGE_PERMISSION))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[ItemPublic]:
    item = await ItemService(session, membership.org_id).update_item(
        membership.user_id, item_id, body
    )
    await session.commit()
    return Envelope(
        data=ItemPublic.model_validate(item),
        meta=Meta(request_id=_request_id(request)),
    )


@router.delete("/{item_id}")
@api_limit
async def delete_item(
    request: Request,
    item_id: UUID,
    membership: Annotated[Membership, Depends(require_permission(MANAGE_PERMISSION))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[None]:
    await ItemService(session, membership.org_id).delete_item(membership.user_id, item_id)
    await session.commit()
    return Envelope(data=None, meta=Meta(request_id=_request_id(request)))
