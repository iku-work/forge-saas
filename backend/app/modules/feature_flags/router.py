"""
HTTP layer for the feature_flags module (T-120): a single read-only
GET /orgs/{org_id}/feature-flags endpoint returning the fully resolved flag
map for the addressed org. Path-keyed and membership-gated via
app.modules.tenancy.router.get_org_membership (same "org id is the
addressed resource" reasoning as every other /orgs/{org_id}/... route),
not the X-Org-ID-header convention app.modules.billing.dependencies uses.

Mutating the map (admin overrides) is T-122's job -- this router is
deliberately read-only.
"""

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.dependencies import get_redis
from app.core.envelope import Envelope, Meta
from app.middleware.rate_limit import api_limit
from app.modules.feature_flags.schemas import FeatureFlagsResponse
from app.modules.feature_flags.service import FeatureFlagService
from app.modules.tenancy.models import Membership, Organization
from app.modules.tenancy.router import get_org_membership

router = APIRouter(tags=["feature_flags"])


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid4())


@router.get("/orgs/{org_id}/feature-flags")
@api_limit
async def list_feature_flags(
    request: Request,
    org_and_membership: Annotated[tuple[Organization, Membership], Depends(get_org_membership)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[FeatureFlagsResponse]:
    org, _ = org_and_membership
    flags = await FeatureFlagService(session, redis).all_enabled(org)
    return Envelope(
        data=FeatureFlagsResponse(flags=flags), meta=Meta(request_id=_request_id(request))
    )
