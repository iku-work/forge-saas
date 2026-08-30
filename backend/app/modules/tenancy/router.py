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
from app.core.dependencies import (
    get_current_user,
    get_redis,
    invalidate_org_cache,
    invalidate_user_cache,
    reject_during_impersonation,
    require_recent_auth,
)
from app.core.envelope import Envelope, Meta
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.core.principal import CurrentPrincipal, require_scope
from app.middleware.rate_limit import api_limit
from app.modules.api_keys.scopes import Scope
from app.modules.auth.models import User
from app.modules.auth.permissions import ROLE_PERMISSIONS, Permission
from app.modules.tenancy.models import Membership, Organization
from app.modules.tenancy.schemas import (
    CreateOrgRequest,
    InvitationPreview,
    InvitationPublic,
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


@router.post("/orgs", dependencies=[Depends(reject_during_impersonation("data_mutation"))])
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


# --- the public surface (T-311) ---------------------------------------------
#
# The two routes below are the *only* ones in this module an API key can call,
# and `require_scope` is the whole of what says so. Every other route here
# resolves a session through `get_org_membership`, unchanged, and rejects a
# key outright.
#
# `require_scope` is dual-principal rather than key-only: a session passes it
# by holding the Permission the scope maps onto (app.modules.api_keys.scopes),
# which for both of these is MEMBERS_READ -- the permission every role already
# has. So opening these to keys did not widen what any *user* can do, and that
# is checked from the outside rather than asserted here.
#
# One thing did change, and it is worth knowing: these routes used to resolve
# the organization and the membership together through TenancyService. The
# membership check now lives in `require_scope`, so `list_members` stopped
# loading an Organization row it never read -- one query fewer, which is why
# its entry in tests/test_query_budgets.py went from 5 to 4.


@router.get(
    "/orgs/{org_id}",
    description=(
        "The organization record. Callable with an API key holding the "
        "`orgs:read` scope, or with a session belonging to the organization."
    ),
)
@api_limit
async def get_org(
    request: Request,
    org_id: UUID,
    principal: Annotated[  # noqa: ARG001 -- gates the route and proves org membership; the org comes from the path
        CurrentPrincipal, Depends(require_scope(Scope.ORGS_READ))
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[OrganizationPublic]:
    org = await TenancyService(session).get_org(org_id)
    if org is None:
        # Not reachable through require_scope for a live membership or key --
        # both are rows whose org_id points here -- but an organization
        # soft-deleted between the two reads lands exactly here, and a 404 is
        # the honest answer to "this organization is gone".
        raise NotFoundError("errors.org_not_found", fallback="Organization not found")
    return Envelope(
        data=OrganizationPublic.model_validate(org), meta=Meta(request_id=_request_id(request))
    )


@router.patch(
    "/orgs/{org_id}", dependencies=[Depends(reject_during_impersonation("data_mutation"))]
)
@api_limit
async def update_org(  # noqa: PLR0913 — redis (cache invalidation, T-240) on top of the usual request+org_id+body+permission+session params
    request: Request,
    org_id: UUID,
    body: UpdateOrgRequest,
    membership: Annotated[  # noqa: ARG001 — required to gate on SETTINGS_MANAGE; org comes from path
        Membership, Depends(require_org_permission(Permission.SETTINGS_MANAGE))
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[OrganizationPublic]:
    org = await TenancyService(session).update_org(org_id, **body.model_dump(exclude_unset=True))
    await session.commit()
    await invalidate_org_cache(redis, org_id)  # T-240: get_current_org caches this row
    return Envelope(
        data=OrganizationPublic.model_validate(org), meta=Meta(request_id=_request_id(request))
    )


@router.get(
    "/orgs/{org_id}/members",
    description=(
        "The organization's members, cursor-paginated. Callable with an API key "
        "holding the `members:read` scope, or with a session belonging to the "
        "organization."
    ),
)
@api_limit
async def list_members(  # noqa: PLR0913 — cursor pagination (§0.3) needs after+limit on top of the usual params
    request: Request,
    org_id: UUID,
    principal: Annotated[  # noqa: ARG001 — gates the route and proves org membership; org comes from path
        CurrentPrincipal, Depends(require_scope(Scope.MEMBERS_READ))
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


@router.patch(
    "/orgs/{org_id}/members/{membership_id}",
    dependencies=[Depends(reject_during_impersonation("data_mutation"))],
)
@api_limit
async def update_member_role(  # noqa: PLR0913 — redis (cache invalidation, T-240) on top of the usual request+membership_id+body+actor+session params
    request: Request,
    membership_id: UUID,
    body: UpdateMembershipRequest,
    actor: Annotated[Membership, Depends(require_org_permission(Permission.MEMBERS_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[MembershipPublic]:
    updated = await TenancyService(session).change_role(actor, membership_id, body.role)
    await session.commit()
    # T-240 matrix, "role/permission change". The cached User row does not
    # itself carry a role -- get_current_org resolves the membership from the
    # DB on every request, deliberately (see its comment) -- so today this
    # only guarantees the target's cached row can't outlive a permission
    # change. It is wired anyway because the day anything permission-derived
    # is added to that payload, the invalidation has to already be here.
    await invalidate_user_cache(redis, updated.user_id)
    return Envelope(
        data=MembershipPublic.model_validate(updated), meta=Meta(request_id=_request_id(request))
    )


@router.delete(
    "/orgs/{org_id}/members/{membership_id}",
    dependencies=[Depends(reject_during_impersonation("data_mutation"))],
)
@api_limit
async def remove_member(
    request: Request,
    membership_id: UUID,
    actor: Annotated[Membership, Depends(require_org_permission(Permission.MEMBERS_MANAGE))],
    _recent_auth: Annotated[None, Depends(require_recent_auth(max_age_minutes=5))],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[None]:
    removed_user_id = await TenancyService(session).remove_member(actor, membership_id)
    await session.commit()
    await invalidate_user_cache(redis, removed_user_id)  # see update_member_role above
    return Envelope(data=None, meta=Meta(request_id=_request_id(request)))


@router.post(
    "/orgs/{org_id}/invitations", dependencies=[Depends(reject_during_impersonation("notifying"))]
)
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


@router.get("/orgs/{org_id}/invitations")
@api_limit
async def list_invitations(  # noqa: PLR0913 — cursor pagination (§0.3) needs after+limit on top of the usual params
    request: Request,
    org_id: UUID,
    membership: Annotated[  # noqa: ARG001 — required to gate on MEMBERS_MANAGE; org comes from path
        Membership, Depends(require_org_permission(Permission.MEMBERS_MANAGE))
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    after: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Envelope[list[InvitationPublic]]:
    # MEMBERS_MANAGE, not the MEMBERS_READ that gates the member list: this
    # response is keyed on the invitee's *email address*, which MembershipPublic
    # deliberately never exposes, and it is the management view of a flow only
    # MEMBERS_MANAGE can start (see create_invitation above).
    invitations, next_cursor = await TenancyService(session).list_pending_invitations(
        org_id, after, limit
    )
    data = [
        InvitationPublic(
            id=invitation.id,
            org_id=invitation.org_id,
            email=invitation.email,
            role=invitation.role,
            invited_by=invitation.invited_by,
            expires_at=invitation.expires_at,
            created_at=invitation.created_at,
        )
        for invitation in invitations
    ]
    meta = Meta(
        request_id=_request_id(request),
        next_cursor=str(next_cursor) if next_cursor is not None else None,
    )
    return Envelope(data=data, meta=meta)


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


@router.post(
    "/invitations/{token}/accept",
    dependencies=[Depends(reject_during_impersonation("data_mutation"))],
)
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
