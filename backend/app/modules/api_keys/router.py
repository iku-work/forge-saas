"""
HTTP layer for the api_keys module (T-310): the management surface, under
/api/v1/api-keys/.

These routes are *not* part of the public API. They are how a signed-in
administrator mints, lists, renames and revokes credentials, so every one of
them is gated by `require_permission` and a session, exactly like the rest of
the product -- a key that could mint another key is a privilege-escalation
primitive, and nothing here carries `require_scope`.
`tests/modules/test_api_key_auth.py::test_a_key_cannot_manage_keys` asserts
that from the outside.

Everything else follows the house pattern the scaffold documents
(app/modules/_template/router.py): an `Envelope` on every response, cursor
pagination on the list, `@api_limit`, `await session.commit()` in the router
rather than the service, and `reject_during_impersonation` on every write --
a support agent inside an impersonation session must not mint a credential
that outlives the session.

The one route that is not CRUD is `POST /{key_id}/revoke`. Revocation is not
a soft delete and not a field edit: it is the incident-response verb, and it
deserves a URL somebody can find under pressure.
"""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.dependencies import get_redis, reject_during_impersonation, require_permission
from app.core.envelope import Envelope, Meta
from app.middleware.rate_limit import api_limit
from app.modules.auth.permissions import Permission
from app.modules.tenancy.models import Membership

from .schemas import ApiKeyCreated, ApiKeyCreateRequest, ApiKeyPublic, ApiKeyUpdateRequest
from .scopes import ALL_SCOPES
from .service import ApiKeyService

# Reading the list shows prefixes and scopes -- what a key can reach, which
# is settings-level information rather than membership-level. Both
# permissions are SETTINGS_MANAGE for that reason, which is also the
# "org-manage permission" T-313 gates its page on: owner and admin, never a
# plain member.
READ_PERMISSION = Permission.SETTINGS_MANAGE
MANAGE_PERMISSION = Permission.SETTINGS_MANAGE

router = APIRouter(prefix="/api-keys", tags=["api_keys"])


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid4())


@router.post(
    "",
    dependencies=[Depends(reject_during_impersonation("data_mutation"))],
    description=(
        "Creates an API key and returns it **once**. The key is not stored and "
        "cannot be retrieved again; every later read returns the prefix only. "
        f"Valid scopes: {', '.join(ALL_SCOPES)}."
    ),
)
@api_limit
async def create_api_key(
    request: Request,
    body: ApiKeyCreateRequest,
    membership: Annotated[Membership, Depends(require_permission(MANAGE_PERMISSION))],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[ApiKeyCreated]:
    service = ApiKeyService(session, membership.org_id, redis)
    key, presented = await service.create_key(membership.user_id, body)
    await session.commit()
    return Envelope(
        data=ApiKeyCreated(key=presented, api_key=ApiKeyPublic.model_validate(key)),
        meta=Meta(request_id=_request_id(request)),
    )


@router.get("")
@api_limit
async def list_api_keys(
    request: Request,
    membership: Annotated[Membership, Depends(require_permission(READ_PERMISSION))],
    session: Annotated[AsyncSession, Depends(get_session)],
    after: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Envelope[list[ApiKeyPublic]]:
    keys, next_cursor = await ApiKeyService(session, membership.org_id).list_keys(after, limit)
    return Envelope(
        data=[ApiKeyPublic.model_validate(key) for key in keys],
        meta=Meta(
            request_id=_request_id(request),
            next_cursor=str(next_cursor) if next_cursor is not None else None,
        ),
    )


@router.get("/{key_id}")
@api_limit
async def get_api_key(
    request: Request,
    key_id: UUID,
    membership: Annotated[Membership, Depends(require_permission(READ_PERMISSION))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[ApiKeyPublic]:
    key = await ApiKeyService(session, membership.org_id).get_key(key_id)
    return Envelope(
        data=ApiKeyPublic.model_validate(key), meta=Meta(request_id=_request_id(request))
    )


@router.patch("/{key_id}", dependencies=[Depends(reject_during_impersonation("data_mutation"))])
@api_limit
async def update_api_key(  # noqa: PLR0913 -- redis (cache invalidation, T-240) on top of the usual params
    request: Request,
    key_id: UUID,
    body: ApiKeyUpdateRequest,
    membership: Annotated[Membership, Depends(require_permission(MANAGE_PERMISSION))],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[ApiKeyPublic]:
    service = ApiKeyService(session, membership.org_id, redis)
    key = await service.update_key(membership.user_id, key_id, body)
    await session.commit()
    return Envelope(
        data=ApiKeyPublic.model_validate(key), meta=Meta(request_id=_request_id(request))
    )


@router.post(
    "/{key_id}/revoke",
    dependencies=[Depends(reject_during_impersonation("data_mutation"))],
    description=(
        "Revokes a key immediately. The row stays visible with `revoked_at` set, "
        "so an incident review can still see what the key could reach. Idempotent: "
        "revoking an already-revoked key is a 200 and does not move the timestamp."
    ),
)
@api_limit
async def revoke_api_key(
    request: Request,
    key_id: UUID,
    membership: Annotated[Membership, Depends(require_permission(MANAGE_PERMISSION))],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[ApiKeyPublic]:
    service = ApiKeyService(session, membership.org_id, redis)
    key = await service.revoke_key(membership.user_id, key_id)
    await session.commit()
    return Envelope(
        data=ApiKeyPublic.model_validate(key), meta=Meta(request_id=_request_id(request))
    )


@router.delete("/{key_id}", dependencies=[Depends(reject_during_impersonation("data_mutation"))])
@api_limit
async def delete_api_key(
    request: Request,
    key_id: UUID,
    membership: Annotated[Membership, Depends(require_permission(MANAGE_PERMISSION))],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[None]:
    service = ApiKeyService(session, membership.org_id, redis)
    await service.delete_key(membership.user_id, key_id)
    await session.commit()
    return Envelope(data=None, meta=Meta(request_id=_request_id(request)))
