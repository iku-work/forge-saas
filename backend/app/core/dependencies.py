"""
RBAC enforcement (T-021): resolves the authenticated user from a bearer JWT
(§0.9: 60s Redis cache on this hot read), resolves the organization +
membership named by the X-Org-ID header, and gates a route on a required
Permission via the role -> permission map in app.modules.auth.permissions.

Four-layer rule (§0.1) note: these are FastAPI dependencies wired directly
into routers, not service-layer code, so they go straight to the repository
layer the same way app.modules.auth.service does.
"""

import json
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Header, Request
from fastapi.security import OAuth2PasswordBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.exceptions import AuthenticationError, DomainError, PermissionDeniedError
from app.modules.auth.models import User
from app.modules.auth.permissions import ROLE_PERMISSIONS, Permission
from app.modules.auth.repository import UserRepository
from app.modules.auth.utils import verify_access_token
from app.modules.tenancy.models import Membership, Organization
from app.modules.tenancy.repository import MembershipRepository, OrganizationRepository

_USER_CACHE_TTL_SECONDS = 60
_USER_DATETIME_FIELDS = (
    "created_at",
    "updated_at",
    "deleted_at",
    "email_verified_at",
    "locked_until",
)

# tokenUrl is only used to populate the Swagger "Authorize" dialog; the real
# login endpoint (T-022) takes a JSON body, not an OAuth2 password form.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


async def get_redis(request: Request) -> Redis:
    redis: Redis = request.app.state.redis
    return redis


def _hydrate_cached_user(cached: str) -> User:
    # SQLModel table=True classes skip their own pydantic coercion on
    # model_validate_json — UUID/datetime fields come back as the raw JSON
    # strings (e.g. user.id ends up a str, not a UUID), which silently
    # breaks anything downstream that uses them as such (e.g. binding
    # user.id into a UUID-typed query parameter). Construct from a plain
    # dict instead, coercing the fields that matter ourselves.
    raw: dict[str, Any] = json.loads(cached)  # arbitrary JSON; coerced field-by-field below
    raw["id"] = UUID(raw["id"])
    for field in _USER_DATETIME_FIELDS:
        if raw.get(field) is not None:
            raw[field] = datetime.fromisoformat(raw[field])
    return User(**raw)


def _user_cache_key(user_id: UUID) -> str:
    return f"user:{user_id}"


async def invalidate_user_cache(redis: Redis, user_id: UUID) -> None:
    """Call after any write to a User row a request handler can reach
    directly -- get_current_user() below caches the row for
    _USER_CACHE_TTL_SECONDS, so a write that skips this leaves a stale copy
    readable from /auth/me (and request.state.locale resolution) until the
    TTL expires."""
    await redis.delete(_user_cache_key(user_id))


async def get_current_user(
    request: Request,
    token: Annotated[str | None, Depends(oauth2_scheme)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> User:
    if token is None:
        raise AuthenticationError("errors.missing_bearer_token", fallback="Missing bearer token")

    payload = verify_access_token(token)
    cache_key = _user_cache_key(payload.user_id)

    cached = await redis.get(cache_key)
    if cached is not None:
        user = _hydrate_cached_user(cached)
    else:
        fetched = await UserRepository(session).get_by_id(payload.user_id)
        if fetched is None:
            raise AuthenticationError("errors.user_not_found", fallback="User not found")
        await redis.set(cache_key, fetched.model_dump_json(), ex=_USER_CACHE_TTL_SECONDS)
        user = fetched

    # Set as soon as a user is resolved -- a known user's preferred locale
    # always wins over the Accept-Language fallback app.middleware.locale.
    # LocaleMiddleware already attached, even for a request that turns out
    # to fail the is_active check right below (§T-068 resolution order).
    if user.locale:
        request.state.locale = user.locale

    if not user.is_active or user.deleted_at is not None:
        raise AuthenticationError("errors.account_inactive", fallback="Account is inactive")
    return user


async def get_current_org(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_org_id: Annotated[UUID | None, Header(alias="X-Org-ID")] = None,
) -> tuple[Organization, Membership]:
    if x_org_id is None:
        raise DomainError("errors.org_header_required", fallback="X-Org-ID header is required")

    org = await OrganizationRepository(session).get_by_id(x_org_id)
    membership = await MembershipRepository(session).get_by_user_and_org(user.id, x_org_id)
    if org is None or membership is None:
        raise PermissionDeniedError(
            "errors.not_org_member", fallback="Not a member of this organization"
        )
    return org, membership


def require_permission(permission: Permission) -> Callable[..., Awaitable[Membership]]:
    async def checker(
        org_and_membership: Annotated[tuple[Organization, Membership], Depends(get_current_org)],
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
