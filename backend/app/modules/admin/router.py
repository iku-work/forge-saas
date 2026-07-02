"""
HTTP layer for /api/v1/admin/ -- the first routes to live under that prefix
(T-122's feature-flag overrides). Gated by
app.core.dependencies.require_superadmin (T-140), not the org-membership
checks every other router in this codebase uses, and deliberately excluded
from the OpenAPI schema (include_in_schema=False on the router itself --
this app builds one process-wide cached schema (see app.main._custom_openapi),
so there's no per-token schema variance to filter; hiding it from everyone
trivially satisfies "not shown to non-admin tokens").

T-141 adds the org/user list-and-detail admin endpoints to this same file;
T-142 adds impersonation.
"""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.dependencies import get_redis, require_superadmin
from app.core.envelope import Envelope, Meta
from app.core.exceptions import NotFoundError
from app.middleware.rate_limit import api_limit
from app.modules.admin.schemas import AdminFeatureFlagsResponse, SetFeatureFlagOverrideRequest
from app.modules.admin.service import AdminFeatureFlagService
from app.modules.auth.models import User
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
