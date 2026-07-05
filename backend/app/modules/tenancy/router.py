"""
HTTP layer for organizations, memberships, and the invitation flow (T-027).

Every route under /orgs/{org_id}/... identifies its target organization by a
path parameter, never the X-Org-ID header app.core.dependencies uses
elsewhere -- the org id *is* the addressed resource here, so resolving
membership against anything other than that path id would let an
authenticated member of one org read or mutate another org's data (the
cross-tenant case this task's tests guard against). get_org_membership below
is this router's own org-resolution dependency for that reason: it mirrors
app.core.dependencies.get_current_org / require_permission, but keyed off
the path rather than a header, and it goes through TenancyService rather
than the repositories directly so it still respects §0.1's router/dependency
-> service -> repository layering.
"""

from collections.abc import Awaitable, Callable
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.dependencies import get_current_user, get_redis
from app.core.envelope import Envelope, Meta
from app.core.exceptions import PermissionDeniedError
from app.middleware.rate_limit import api_limit
from app.modules.auth.models import User
from app.modules.auth.permissions import ROLE_PERMISSIONS, Permission
from app.modules.tenancy.models import Membership, Organization
from app.modules.tenancy.schemas import (
    CreateOrgRequest,
    InvitationPreview,
    InviteRequest,
    MembershipPublic,
    OrganizationPublic,
    UpdateMembershipRequest,
    UpdateOrgRequest,
)
from app.modules.tenancy.service import TenancyService

router = APIRouter(tags=["tenancy"])


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid4())


async def get_org_membership(
    org_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> tuple[Organization, Membership]:
    return await TenancyService(session).get_membership_or_raise(org_id, user.id)


def require_org_permission(permission: Permission) -> Callable[..., Awaitable[Membership]]:
    async def checker(
        org_and_membership: Annotated[tuple[Organization, Membership], Depends(get_org_membership)],
    ) -> Membership:
        _, membership = org_and_membership
        if permission not in ROLE_PERMISSIONS[membership.role]:
            raise PermissionDeniedError(
                "errors.missing_permission",
                fallback=f"Missing permission: {permission.value}",
                params={"permission": permission.value},
            )
        return membership

    return checker


@router.post("/orgs")
@api_limit
async def create_org(
    request: Request,
    body: CreateOrgRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[OrganizationPublic]:
    org = await TenancyService(session).create_org(user, body.name)
    await session.commit()
    return Envelope(
        data=OrganizationPublic.model_validate(org), meta=Meta(request_id=_request_id(request))
    )


@router.get("/orgs")
@api_limit
async def list_my_orgs(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[list[OrganizationPublic]]:
    orgs = await TenancyService(session).list_orgs_for_user(user.id)
    data = [OrganizationPublic.model_validate(org) for org in orgs]
    return Envelope(data=data, meta=Meta(request_id=_request_id(request)))


@router.get("/orgs/{org_id}")
@api_limit
async def get_org(
    request: Request,
    org_and_membership: Annotated[tuple[Organization, Membership], Depends(get_org_membership)],
) -> Envelope[OrganizationPublic]:
    org, _ = org_and_membership
    return Envelope(
        data=OrganizationPublic.model_validate(org), meta=Meta(request_id=_request_id(request))
    )


@router.patch("/orgs/{org_id}")
@api_limit
async def update_org(
    request: Request,
    org_id: UUID,
    body: UpdateOrgRequest,
    membership: Annotated[  # noqa: ARG001 — required to gate on SETTINGS_MANAGE; org comes from path
        Membership, Depends(require_org_permission(Permission.SETTINGS_MANAGE))
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[OrganizationPublic]:
    org = await TenancyService(session).update_org(org_id, **body.model_dump(exclude_unset=True))
    await session.commit()
    return Envelope(
        data=OrganizationPublic.model_validate(org), meta=Meta(request_id=_request_id(request))
    )


@router.get("/orgs/{org_id}/members")
@api_limit
async def list_members(  # noqa: PLR0913 — cursor pagination (§0.3) needs after+limit on top of the usual params
    request: Request,
    org_id: UUID,
    membership: Annotated[  # noqa: ARG001 — required to gate on MEMBERS_READ; org comes from path
        Membership, Depends(require_org_permission(Permission.MEMBERS_READ))
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    after: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Envelope[list[MembershipPublic]]:
    members, next_cursor = await TenancyService(session).list_members(org_id, after, limit)
    data = [MembershipPublic.model_validate(m) for m in members]
    meta = Meta(
        request_id=_request_id(request),
        next_cursor=str(next_cursor) if next_cursor is not None else None,
    )
    return Envelope(data=data, meta=meta)


@router.patch("/orgs/{org_id}/members/{membership_id}")
@api_limit
async def update_member_role(
    request: Request,
    membership_id: UUID,
    body: UpdateMembershipRequest,
    actor: Annotated[Membership, Depends(require_org_permission(Permission.MEMBERS_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[MembershipPublic]:
    updated = await TenancyService(session).change_role(actor, membership_id, body.role)
    await session.commit()
    return Envelope(
        data=MembershipPublic.model_validate(updated), meta=Meta(request_id=_request_id(request))
    )


@router.delete("/orgs/{org_id}/members/{membership_id}")
@api_limit
async def remove_member(
    request: Request,
    membership_id: UUID,
    actor: Annotated[Membership, Depends(require_org_permission(Permission.MEMBERS_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[None]:
    await TenancyService(session).remove_member(actor, membership_id)
    await session.commit()
    return Envelope(data=None, meta=Meta(request_id=_request_id(request)))


@router.post("/orgs/{org_id}/invitations")
@api_limit
async def create_invitation(  # noqa: PLR0913 — redis (for the inviter notification) on top of the usual request+org_id+body+actor+session params
    request: Request,
    org_id: UUID,
    body: InviteRequest,
    actor: Annotated[Membership, Depends(require_org_permission(Permission.MEMBERS_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[None]:
    await TenancyService(session).invite(org_id, actor, body.email, body.role, redis)
    await session.commit()
    return Envelope(data=None, meta=Meta(request_id=_request_id(request)))


@router.get("/invitations/{token}")
@api_limit
async def preview_invitation(
    request: Request,
    token: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[InvitationPreview]:
    invitation, org = await TenancyService(session).preview_invitation(token)
    data = InvitationPreview(
        org_id=org.id,
        org_name=org.name,
        email=invitation.email,
        role=invitation.role,
        expires_at=invitation.expires_at,
    )
    return Envelope(data=data, meta=Meta(request_id=_request_id(request)))


@router.post("/invitations/{token}/accept")
@api_limit
async def accept_invitation(
    request: Request,
    token: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[MembershipPublic]:
    membership = await TenancyService(session).accept_invitation(token, user)
    await session.commit()
    return Envelope(
        data=MembershipPublic.model_validate(membership),
        meta=Meta(request_id=_request_id(request)),
    )
