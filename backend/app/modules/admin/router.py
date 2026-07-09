"""
HTTP layer for /api/v1/admin/ -- the first routes to live under that prefix
(T-122's feature-flag overrides). Gated by
app.core.dependencies.require_superadmin (T-140), not the org-membership
checks every other router in this codebase uses, and deliberately excluded
from the OpenAPI schema (include_in_schema=False on the router itself --
this app builds one process-wide cached schema (see app.main._custom_openapi),
so there's no per-token schema variance to filter; hiding it from everyone
trivially satisfies "not shown to non-admin tokens").

T-141 adds the org/user list-and-detail admin endpoints plus the
plan-override/extend-trial/comp-credit writes to this same file; T-142 adds
impersonation.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.dependencies import get_current_user, get_redis, oauth2_scheme, require_superadmin
from app.core.envelope import Envelope, Meta
from app.core.exceptions import AuthenticationError, NotFoundError
from app.middleware.rate_limit import api_limit
from app.modules.admin.schemas import (
    AdminAuditFeedEntry,
    AdminCompCreditRequest,
    AdminDashboardStats,
    AdminDisableTotpRequest,
    AdminExtendTrialRequest,
    AdminFeatureFlagsResponse,
    AdminImpersonateRequest,
    AdminImpersonateResponse,
    AdminOrgDetail,
    AdminOrgSummary,
    AdminSetOrgPlanRequest,
    AdminUserDetail,
    AdminUserSummary,
    SetFeatureFlagOverrideRequest,
)
from app.modules.admin.service import (
    AdminAccountRecoveryService,
    AdminBillingOverrideService,
    AdminDashboardService,
    AdminDirectoryService,
    AdminFeatureFlagService,
    AdminImpersonationService,
)
from app.modules.auth.models import User
from app.modules.auth.repository import UserRepository
from app.modules.auth.schemas import UserPublic
from app.modules.tenancy.models import Organization
from app.modules.tenancy.repository import OrganizationRepository

router = APIRouter(prefix="/admin", tags=["admin"], include_in_schema=False)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid4())


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def _get_org_or_404(org_id: UUID, session: AsyncSession) -> Organization:
    org = await OrganizationRepository(session).get_by_id(org_id)
    if org is None:
        raise NotFoundError("errors.org_not_found", fallback="Organization not found")
    return org


async def _get_user_or_404(user_id: UUID, session: AsyncSession) -> User:
    user = await UserRepository(session).get_by_id(user_id)
    if user is None:
        raise NotFoundError("errors.user_not_found", fallback="User not found")
    return user


@router.get("/orgs/{org_id}/feature-flags")
@api_limit
async def get_org_feature_flags(
    request: Request,
    org_id: UUID,
    superadmin: Annotated[User, Depends(require_superadmin)],  # noqa: ARG001 -- gate only
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[AdminFeatureFlagsResponse]:
    org = await _get_org_or_404(org_id, session)
    flags = await AdminFeatureFlagService(session, redis).resolved_flags(org)
    return Envelope(
        data=AdminFeatureFlagsResponse(flags=flags), meta=Meta(request_id=_request_id(request))
    )


@router.put("/orgs/{org_id}/feature-flags/{flag}")
@api_limit
async def set_org_feature_flag(  # noqa: PLR0913 -- request+path+body+auth+session+redis, all required
    request: Request,
    org_id: UUID,
    flag: str,
    body: SetFeatureFlagOverrideRequest,
    superadmin: Annotated[User, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[None]:
    org = await _get_org_or_404(org_id, session)
    await AdminFeatureFlagService(session, redis).set_override(
        org=org,
        flag=flag,
        enabled=body.enabled,
        actor_id=superadmin.id,
        ip=_client_ip(request),
        ua=request.headers.get("user-agent", ""),
    )
    await session.commit()
    return Envelope(data=None, meta=Meta(request_id=_request_id(request)))


@router.delete("/orgs/{org_id}/feature-flags/{flag}")
@api_limit
async def delete_org_feature_flag(  # noqa: PLR0913 -- request+path+auth+session+redis, all required
    request: Request,
    org_id: UUID,
    flag: str,
    superadmin: Annotated[User, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[None]:
    org = await _get_org_or_404(org_id, session)
    await AdminFeatureFlagService(session, redis).delete_override(
        org=org,
        flag=flag,
        actor_id=superadmin.id,
        ip=_client_ip(request),
        ua=request.headers.get("user-agent", ""),
    )
    await session.commit()
    return Envelope(data=None, meta=Meta(request_id=_request_id(request)))


# ---- T-141: org/user directory ----------------------------------------------


@router.get("/orgs")
@api_limit
async def list_orgs(  # noqa: PLR0913 -- cursor pagination (§0.3) plus the three filters T-141 specifies
    request: Request,
    superadmin: Annotated[User, Depends(require_superadmin)],  # noqa: ARG001 -- gate only
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
    after: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str | None, Query()] = None,
    plan: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
) -> Envelope[list[AdminOrgSummary]]:
    summaries, next_cursor = await AdminDirectoryService(session, redis).list_orgs(
        after, limit, q=q, plan_slug=plan, status=status
    )
    meta = Meta(
        request_id=_request_id(request),
        next_cursor=str(next_cursor) if next_cursor is not None else None,
    )
    return Envelope(data=summaries, meta=meta)


@router.get("/orgs/{org_id}")
@api_limit
async def get_org_detail(
    request: Request,
    org_id: UUID,
    superadmin: Annotated[User, Depends(require_superadmin)],  # noqa: ARG001 -- gate only
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[AdminOrgDetail]:
    org = await _get_org_or_404(org_id, session)
    detail = await AdminDirectoryService(session, redis).get_org_detail(org)
    return Envelope(data=detail, meta=Meta(request_id=_request_id(request)))


@router.get("/users")
@api_limit
async def list_users(  # noqa: PLR0913 -- cursor pagination (§0.3) plus the two filters T-141 specifies
    request: Request,
    superadmin: Annotated[User, Depends(require_superadmin)],  # noqa: ARG001 -- gate only
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
    after: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str | None, Query()] = None,
    active: Annotated[bool | None, Query()] = None,
) -> Envelope[list[AdminUserSummary]]:
    summaries, next_cursor = await AdminDirectoryService(session, redis).list_users(
        after, limit, q=q, active=active
    )
    meta = Meta(
        request_id=_request_id(request),
        next_cursor=str(next_cursor) if next_cursor is not None else None,
    )
    return Envelope(data=summaries, meta=meta)


@router.get("/users/{user_id}")
@api_limit
async def get_user_detail(
    request: Request,
    user_id: UUID,
    superadmin: Annotated[User, Depends(require_superadmin)],  # noqa: ARG001 -- gate only
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[AdminUserDetail]:
    user = await _get_user_or_404(user_id, session)
    detail = await AdminDirectoryService(session, redis).get_user_detail(user)
    return Envelope(data=detail, meta=Meta(request_id=_request_id(request)))


# ---- T-141: plan override / trial / comp-credit -----------------------------


@router.put("/orgs/{org_id}/plan")
@api_limit
async def set_org_plan(  # noqa: PLR0913 -- request+path+body+auth+session+redis, all required
    request: Request,
    org_id: UUID,
    body: AdminSetOrgPlanRequest,
    superadmin: Annotated[User, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[None]:
    org = await _get_org_or_404(org_id, session)
    await AdminBillingOverrideService(session, redis).set_plan(
        org=org,
        plan_slug=body.plan_slug,
        billing_interval=body.billing_interval,
        free_override=body.free_override,
        expires_at=body.expires_at,
        actor_id=superadmin.id,
        ip=_client_ip(request),
        ua=request.headers.get("user-agent", ""),
    )
    await session.commit()
    return Envelope(data=None, meta=Meta(request_id=_request_id(request)))


@router.post("/orgs/{org_id}/extend-trial")
@api_limit
async def extend_org_trial(  # noqa: PLR0913 -- request+path+body+auth+session+redis, all required
    request: Request,
    org_id: UUID,
    body: AdminExtendTrialRequest,
    superadmin: Annotated[User, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[None]:
    org = await _get_org_or_404(org_id, session)
    await AdminBillingOverrideService(session, redis).extend_trial(
        org=org,
        days=body.days,
        actor_id=superadmin.id,
        ip=_client_ip(request),
        ua=request.headers.get("user-agent", ""),
    )
    await session.commit()
    return Envelope(data=None, meta=Meta(request_id=_request_id(request)))


@router.post("/orgs/{org_id}/comp-credit")
@api_limit
async def issue_org_comp_credit(  # noqa: PLR0913 -- request+path+body+auth+session+redis, all required
    request: Request,
    org_id: UUID,
    body: AdminCompCreditRequest,
    superadmin: Annotated[User, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[None]:
    org = await _get_org_or_404(org_id, session)
    await AdminBillingOverrideService(session, redis).issue_comp_credit(
        org=org,
        amount_cents=body.amount_cents,
        reason=body.reason,
        actor_id=superadmin.id,
        ip=_client_ip(request),
        ua=request.headers.get("user-agent", ""),
    )
    await session.commit()
    return Envelope(data=None, meta=Meta(request_id=_request_id(request)))


# ---- T-142: user impersonation -----------------------------------------------


@router.post("/users/{user_id}/impersonate")
@api_limit
async def impersonate_user(  # noqa: PLR0913 -- request+path+body+auth+session+redis, all required
    request: Request,
    user_id: UUID,
    body: AdminImpersonateRequest,
    superadmin: Annotated[User, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[AdminImpersonateResponse]:
    target = await _get_user_or_404(user_id, session)
    access_token, expires_at = await AdminImpersonationService(session, redis).start(
        superadmin=superadmin,
        target=target,
        reason=body.reason,
        duration_minutes=body.duration_minutes,
        ip=_client_ip(request),
        ua=request.headers.get("user-agent", ""),
    )
    await session.commit()
    return Envelope(
        data=AdminImpersonateResponse(
            access_token=access_token,
            expires_at=expires_at,
            user=UserPublic.model_validate(target),
        ),
        meta=Meta(request_id=_request_id(request)),
    )


@router.post("/impersonate/end")
@api_limit
async def end_impersonation(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],  # noqa: ARG001 -- requires *some* valid, non-blocklisted token; the impersonation check itself is on the token payload in the service
    token: Annotated[str | None, Depends(oauth2_scheme)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[None]:
    if token is None:
        # Unreachable in practice -- get_current_user above shares this same
        # oauth2_scheme dependency and already raises if it's None -- but
        # kept as an explicit guard rather than an unchecked `str` cast, in
        # case that coupling ever changes.
        raise AuthenticationError("errors.missing_bearer_token", fallback="Missing bearer token")
    await AdminImpersonationService(session, redis).end(
        token=token, ip=_client_ip(request), ua=request.headers.get("user-agent", "")
    )
    await session.commit()
    return Envelope(data=None, meta=Meta(request_id=_request_id(request)))


# ---- T-218: admin-assisted lost-2FA recovery ---------------------------------


@router.post("/users/{user_id}/disable-totp")
@api_limit
async def disable_user_totp(
    request: Request,
    user_id: UUID,
    body: AdminDisableTotpRequest,
    superadmin: Annotated[User, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[None]:
    target = await _get_user_or_404(user_id, session)
    await AdminAccountRecoveryService(session).disable_totp_for_user(
        target=target,
        reason=body.reason,
        actor_id=superadmin.id,
        ip=_client_ip(request),
        ua=request.headers.get("user-agent", ""),
    )
    await session.commit()
    return Envelope(data=None, meta=Meta(request_id=_request_id(request)))


# ---- T-143: dashboard + global audit feed ------------------------------------


@router.get("/dashboard")
@api_limit
async def get_admin_dashboard(
    request: Request,
    superadmin: Annotated[User, Depends(require_superadmin)],  # noqa: ARG001 -- gate only
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[AdminDashboardStats]:
    stats = await AdminDashboardService(session, redis).get_stats()
    return Envelope(data=stats, meta=Meta(request_id=_request_id(request)))


@router.get("/audit")
@api_limit
async def list_admin_audit_feed(  # noqa: PLR0913 -- cursor pagination (§0.3) plus T-143's four filters
    request: Request,
    superadmin: Annotated[User, Depends(require_superadmin)],  # noqa: ARG001 -- gate only
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
    after: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    actor: Annotated[UUID | None, Query()] = None,
    action: Annotated[str | None, Query()] = None,
    org: Annotated[UUID | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
) -> Envelope[list[AdminAuditFeedEntry]]:
    entries, next_cursor = await AdminDirectoryService(session, redis).list_audit_feed(
        after, limit, actor_id=actor, action=action, org_id=org, since=since, until=until
    )
    meta = Meta(
        request_id=_request_id(request),
        next_cursor=str(next_cursor) if next_cursor is not None else None,
    )
    return Envelope(data=entries, meta=meta)
