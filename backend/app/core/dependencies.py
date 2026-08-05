"""
RBAC enforcement (T-021): resolves the authenticated user from a bearer JWT
(§0.9: 60s Redis cache on this hot read), resolves the organization +
membership named by the X-Org-ID header, and gates a route on a required
Permission via the role -> permission map in app.modules.auth.permissions.

Four-layer rule (§0.1) note: these are FastAPI dependencies wired directly
into routers, not service-layer code, so they go straight to the repository
layer the same way app.modules.auth.service does.

T-260 adds `reject_during_impersonation` at the bottom: an authorization gate
that keys off *who is really acting* rather than what they may do, and so sits
next to the rest of them rather than in the admin module that mints the
impersonation token.
"""

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, Header, Request
from fastapi.security import OAuth2PasswordBearer
from loguru import logger
from redis.asyncio import Redis
from sentry_sdk import set_user as set_sentry_user
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache, cache_keys
from app.core.db import get_session
from app.core.exceptions import (
    AuthenticationError,
    DomainError,
    PermissionDeniedError,
    StepUpAuthRequiredError,
)
from app.core.token_blocklist import is_jti_blocked
from app.models._mixins import utcnow
from app.modules.audit.service import AuditService
from app.modules.auth.models import User
from app.modules.auth.permissions import ROLE_PERMISSIONS, Permission
from app.modules.auth.repository import UserRepository, WebAuthnCredentialRepository
from app.modules.auth.utils import AccessTokenPayload, bearer_token, verify_access_token
from app.modules.tenancy.models import Membership, Organization
from app.modules.tenancy.repository import MembershipRepository, OrganizationRepository

# tokenUrl is only used to populate the Swagger "Authorize" dialog; the real
# login endpoint (T-022) takes a JSON body, not an OAuth2 password form.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


async def get_redis(request: Request) -> Redis:
    redis: Redis = request.app.state.redis
    return redis


async def invalidate_user_cache(redis: Redis, user_id: UUID) -> None:
    """Call after any write to a User row -- get_current_user() below caches
    the row for cache.DEFAULT_TTL_SECONDS, so a write that skips this leaves
    a stale copy readable from /auth/me (and request.state.locale resolution)
    until the TTL expires. The full list of writes that must call it is the
    invalidation matrix in app.core.cache_keys' docstring; a deactivation or
    delete that skipped it would keep a disabled account working for the rest
    of the minute, which is why the matrix is tested rather than trusted."""
    await cache.invalidate(redis, cache_keys.user_key(user_id))


async def invalidate_org_cache(redis: Redis, org_id: UUID) -> None:
    """Counterpart for the Organization row get_current_org() caches."""
    await cache.invalidate(redis, cache_keys.org_settings_key(org_id))


async def get_current_user(
    request: Request,
    token: Annotated[str | None, Depends(oauth2_scheme)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> User:
    if token is None:
        raise AuthenticationError("errors.missing_bearer_token", fallback="Missing bearer token")

    payload = verify_access_token(token)
    if await is_jti_blocked(redis, payload.jti):
        # T-142: an ended impersonation token (or any future single-token
        # revocation) lands here -- distinct from refresh-token rotation
        # (SessionRepository), which revokes a whole session, not one jti.
        raise AuthenticationError("errors.token_revoked", fallback="Token has been revoked")
    # T-218: stashed for require_recent_auth below to read without decoding
    # the token a second time -- FastAPI dedups get_current_user across
    # dependencies within one request, so this always runs first.
    request.state.auth_time = payload.auth_time
    cache_key = cache_keys.user_key(payload.user_id)

    cached = await cache.read_json(redis, cache_key)
    if cached is not None:
        user = cache.hydrate(User, cached)
    else:
        # Deliberately not cache.cached_json(): on a miss this returns the
        # session-attached row the repository loaded, not a hydrated copy of
        # it, so a caller that mutates `user` and flushes still persists.
        fetched = await UserRepository(session).get_by_id(payload.user_id)
        if fetched is None:
            raise AuthenticationError("errors.user_not_found", fallback="User not found")
        await cache.write_json(redis, cache_key, cache.dump(fetched))
        user = fetched

    # T-201: id only, never email or name -- Sentry events raised for the
    # rest of this request (including by a nested dependency) get tagged
    # with whichever user this call resolved.
    set_sentry_user({"id": str(user.id)})

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
    redis: Annotated[Redis, Depends(get_redis)],
    x_org_id: Annotated[UUID | None, Header(alias="X-Org-ID")] = None,
) -> tuple[Organization, Membership]:
    if x_org_id is None:
        raise DomainError("errors.org_header_required", fallback="X-Org-ID header is required")

    # T-240: the org row (name, logo, settings JSONB) is read on every
    # org-scoped request and written about as often as a company renames
    # itself. The membership below is *not* cached with it -- it is the
    # tenant-isolation check (§0.2), and a revoked membership must stop
    # working on the next request, not at the end of a TTL.
    async def _load_org() -> cache.JsonDict | None:
        row = await OrganizationRepository(session).get_by_id(x_org_id)
        return cache.dump(row) if row is not None else None

    cached_org = await cache.cached_json(redis, cache_keys.org_settings_key(x_org_id), _load_org)
    org = cache.hydrate(Organization, cached_org) if cached_org is not None else None
    membership = await MembershipRepository(session).get_by_user_and_org(user.id, x_org_id)
    if org is None or membership is None:
        raise PermissionDeniedError(
            "errors.not_org_member", fallback="Not a member of this organization"
        )
    return org, membership


async def require_superadmin(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """
    T-140: a platform-level role, distinct from org-scoped RBAC above --
    gates every route under /api/v1/admin/. Every access (not just mutations)
    is logged with admin_action=True, which app.core.logging's dedicated
    sink routes into admin-audit.log in addition to the normal log stream.

    T-218/ASVS V4.3.1: a superadmin account without a second factor enrolled
    (TOTP or WebAuthn) is rejected outright -- the platform-operator role
    this dependency gates is high enough value that "password alone" isn't
    an acceptable posture for it, regardless of what an individual org's own
    2FA policy allows its members.
    """
    if not user.is_superadmin:
        raise PermissionDeniedError(
            "errors.superadmin_required", fallback="Superadmin access required"
        )
    if not user.totp_enabled:
        has_webauthn = await WebAuthnCredentialRepository(session).exists_for_user(user.id)
        if not has_webauthn:
            raise PermissionDeniedError(
                "errors.superadmin_2fa_required",
                fallback=(
                    "Superadmin accounts must enable two-factor authentication (an "
                    "authenticator app or a security key) before accessing admin features."
                ),
            )
    logger.bind(
        admin_action=True,
        actor_id=str(user.id),
        path=request.url.path,
        query=str(request.url.query),
        request_id=getattr(request.state, "request_id", None),
        target=dict(request.path_params) or None,
    ).info("admin.access")
    return user


def require_recent_auth(max_age_minutes: int) -> Callable[..., Awaitable[None]]:
    """T-218/ASVS V3.7.1/V4.3.3: gates a sensitive action on the caller's
    session having been established (or refreshed via a *real*
    re-authentication -- see Session.auth_time's docstring) within
    `max_age_minutes`. A silently-refreshed access token never advances
    auth_time, so a session idling well past this window still fails here
    even though get_current_user itself accepts the (still otherwise valid)
    access token.
    """

    async def checker(
        request: Request,
        user: Annotated[User, Depends(get_current_user)],  # noqa: ARG001 -- ensures auth_time is set
    ) -> None:
        auth_time: datetime = request.state.auth_time
        if utcnow() - auth_time > timedelta(minutes=max_age_minutes):
            raise StepUpAuthRequiredError(
                "errors.step_up_auth_required",
                fallback=(
                    "This action requires you to have signed in recently. "
                    "Please log in again to continue."
                ),
            )

    return checker


def require_permission(permission: Permission) -> Callable[..., Awaitable[Membership]]:
    """T-219/ASVS V7.1.3, V7.2.2: a denial here is an access-control decision
    (not the RBAC matrix in app.modules.auth.permissions itself), so it gets
    an AuditEvent the same way a denial in require_superadmin already gets a
    log line -- unlike that one, this fires only on the *deny* path, since
    every allowed org-scoped request would otherwise double the audit-write
    volume of the whole API for no security value.
    """

    async def checker(
        request: Request,
        org_and_membership: Annotated[tuple[Organization, Membership], Depends(get_current_org)],
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> Membership:
        org, membership = org_and_membership
        if permission not in ROLE_PERMISSIONS[membership.role]:
            await AuditService(session).log(
                action="permission.denied",
                resource_type="membership",
                resource_id=str(membership.id),
                org_id=org.id,
                actor_id=membership.user_id,
                diff={"permission": permission.value, "role": membership.role.value},
                ip=request.client.host if request.client else None,
                ua=request.headers.get("user-agent"),
                severity="elevated",
            )
            # Same reason as reject_during_impersonation below: without this
            # the raise unwinds get_session's rollback and the denial event
            # never reaches the table it was written for.
            await session.commit()
            raise PermissionDeniedError(
                "errors.missing_permission",
                fallback=f"Missing permission: {permission.value}",
                params={"permission": permission.value},
            )
        return membership

    return checker


# T-260: the four categories of side effect a support agent must not produce
# while wearing someone else's identity. They are labels for *why* a route is
# blocked, not a hierarchy -- nothing branches on the value except the audit
# diff and the error's `details`, which is what makes "which category tripped"
# answerable from the audit feed alone.
#
#   notifying              -- anything that sends mail or a push
#   billing                -- anything that can produce a charge
#   data_mutation          -- credentials, identity, membership, stored data
#   notification_read_state -- marking notifications read (silent state
#                              pollution: the user loses their own unread badge
#                              and never learns why)
BlockedActionKind = Literal["notifying", "billing", "data_mutation", "notification_read_state"]

_BLOCKED_KIND_ATTR = "__impersonation_blocked_kind__"


def blocked_action_kind(call: object) -> str | None:
    """The category `call` blocks, or None if it isn't a T-260 guard at all.

    The one supported way to ask "is this dependency the impersonation
    blocklist?" -- tests/security/test_impersonation_blocklist.py walks every
    route's dependency tree through this, so the marker attribute below stays
    an implementation detail.
    """
    kind = getattr(call, _BLOCKED_KIND_ATTR, None)
    return kind if isinstance(kind, str) else None


def _impersonation_payload(request: Request) -> AccessTokenPayload | None:
    """The bearer token's claims, but only when it is an impersonation token.

    Deliberately decodes the token itself rather than reading what
    app.middleware.impersonation stashed: this guard is the only thing standing
    between a support agent and a charge on someone else's card, and it must
    not fail open if that middleware is ever reordered, removed, or bypassed
    (e.g. a route mounted on a sub-app). The extra decode is an HMAC over a
    short string, on ~40 routes, none of them hot.

    Reads the header directly instead of depending on `oauth2_scheme`, which
    would add a `security` requirement to every guarded route's OpenAPI
    operation -- including the public ones (/auth/forgot-password,
    /auth/magic-link), whose generated clients would then look like they need
    a token.

    An undecodable or expired token is *not* an impersonation session: it is a
    401 waiting to happen in get_current_user, or -- on those public endpoints
    -- simply irrelevant.
    """
    token = bearer_token(request.headers.get("authorization"))
    if token is None:
        return None
    try:
        payload = verify_access_token(token)
    except AuthenticationError:
        return None
    return payload if payload.impersonator_id is not None else None


def reject_during_impersonation(kind: BlockedActionKind) -> Callable[..., Awaitable[None]]:
    """T-260: refuses an action that would produce a user-visible side effect
    the impersonated user never asked for -- an email they didn't request, a
    charge they didn't authorise, a password they didn't change.

    Wired as a route-decorator dependency (`dependencies=[Depends(...)]`)
    rather than a signature parameter, unlike require_recent_auth next door:
    it returns nothing an endpoint could use, ~40 routes carry it, and several
    of them already sit at the PLR0913 parameter limit. The trade-off is that
    it can't be read off the signature -- which is exactly why
    `blocked_action_kind` above exists and why the coverage sweep walks the
    dependency tree instead of the source.

    Deliberately NOT dependent on get_current_user: /auth/forgot-password and
    /auth/magic-link are unauthenticated, and requiring a user there would turn
    a public endpoint into a 401 for everyone. The token is optional here; only
    its `impersonator_id` claim matters.
    """

    async def checker(
        request: Request,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> None:
        payload = _impersonation_payload(request)
        if payload is None:
            return

        await AuditService(session).log(
            action="impersonation.blocked",
            resource_type="user",
            resource_id=str(payload.user_id),
            org_id=None,
            # The impersonated user is the principal the request was made as;
            # the superadmin behind it lands in impersonator_id automatically
            # (app.core.audit_context), the same shape as every other event
            # written during an impersonated request.
            actor_id=payload.user_id,
            diff={"kind": kind, "method": request.method, "path": request.url.path},
            ip=request.client.host if request.client else None,
            ua=request.headers.get("user-agent"),
            severity="elevated",
        )
        # Committed here, not left to the router: raising below unwinds
        # get_session's `except: rollback()`, which would take the audit row
        # with it -- and an unrecorded block is the one outcome this event
        # exists to prevent. Safe to commit the caller's session because no
        # endpoint body has run yet; the only other dependencies that can have
        # touched it are reads.
        await session.commit()
        raise PermissionDeniedError(
            "errors.impersonation_blocked",
            fallback=(
                f"Action '{kind}' is blocked during impersonation. "
                "End impersonation to perform this action as yourself."
            ),
            details={"kind": kind},
        )

    # setattr, not `checker.__impersonation_blocked_kind__ = kind`: the name is
    # a private constant shared with blocked_action_kind above, and a function
    # object has no declared attribute for mypy to accept the direct form.
    setattr(checker, _BLOCKED_KIND_ATTR, kind)
    return checker
