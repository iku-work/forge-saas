"""
HTTP layer for the `enterprise_sso` module (T-331), under /api/v1/auth/sso/.

Three kinds of route in one file, and it is worth naming which is which
before changing anything here:

  * **`/{org_slug}/start` and `/callback`** are *unauthenticated* — they are
    how somebody with no session gets one. They carry `@auth_limit`, the same
    budget every other credential-presenting endpoint in this product has.
  * **`/hint`** is unauthenticated too, and is the only route here that can
    tell a stranger something about an organization. It answers "no" for
    every domain whose connection has not opted in, so the two cases are
    indistinguishable from outside. ADR-0006 decision 10.
  * **`/connection`** is the admin surface: session, `SETTINGS_MANAGE`, and
    `reject_during_impersonation` on every write. A support agent inside an
    impersonation session must not be able to point an organization's *login*
    at an IdP they control — that is the single most valuable write in this
    codebase, and it is why the guard is not negotiable here.

**Scope: web only.** The redirect chain assumes a browser that will follow a
302 to a third party and come back with a cookie. A native app needs
`ASWebAuthenticationSession` / Custom Tabs and a deep-link return on top of
the infrastructure T-261 built; that is a follow-up task, stated here rather
than discovered when a customer asks.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.dependencies import get_redis, reject_during_impersonation, require_permission
from app.core.envelope import Envelope, Meta
from app.core.exceptions import DomainError
from app.middleware.rate_limit import api_limit, auth_limit, sensitive_limit
from app.modules.auth.permissions import Permission
from app.modules.auth.router import _client_ip, _request_id, _set_refresh_cookie
from app.modules.auth.schemas import LoginResponse, TwoFactorRequiredResponse, UserPublic
from app.modules.auth.service import TwoFactorChallenge
from app.modules.tenancy.models import Membership

from .schemas import (
    SsoConnectionPublic,
    SsoConnectionUpsertRequest,
    SsoHintRequest,
    SsoHintResponse,
)
from .service import STATE_TTL_SECONDS, SsoConnectionService, SsoLoginService

# Mirrors T-023's OAuth state cookie exactly, including the reasoning: this
# app has no server-side session to hold CSRF state in, so the signed `state`
# is echoed in a short-lived cookie and the callback compares the two.
_STATE_COOKIE_NAME = "sso_state"
_STATE_COOKIE_PATH = "/api/v1/auth/sso"

MANAGE_PERMISSION = Permission.SETTINGS_MANAGE

router = APIRouter(prefix="/auth/sso", tags=["auth"])


# --- the admin surface -------------------------------------------------------
#
# Registered before "/{org_slug}/start", because Starlette matches in
# registration order and "connection" would otherwise be read as an org slug
# -- the same trap T-216 hit with "/accounts" under "/{provider}".


@router.get("/connection")
@api_limit
async def get_sso_connection(
    request: Request,
    membership: Annotated[Membership, Depends(require_permission(MANAGE_PERMISSION))],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[SsoConnectionPublic | None]:
    connection = await SsoConnectionService(session, membership.org_id, redis).get()
    return Envelope(
        data=SsoConnectionPublic.model_validate(connection) if connection else None,
        meta=Meta(request_id=_request_id(request)),
    )


@router.put(
    "/connection",
    dependencies=[Depends(reject_during_impersonation("data_mutation"))],
    description=(
        "Creates or replaces this organization's identity provider. The "
        "discovery document is fetched and its `issuer` compared against the "
        "one supplied before anything is written, so the most common "
        "misconfiguration fails here rather than for every member at once at "
        "the next sign-in."
    ),
)
@sensitive_limit
async def put_sso_connection(
    request: Request,
    body: SsoConnectionUpsertRequest,
    membership: Annotated[Membership, Depends(require_permission(MANAGE_PERMISSION))],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[SsoConnectionPublic]:
    service = SsoConnectionService(session, membership.org_id, redis)
    connection = await service.upsert(membership.user_id, body)
    await session.commit()
    return Envelope(
        data=SsoConnectionPublic.model_validate(connection),
        meta=Meta(request_id=_request_id(request)),
    )


@router.delete("/connection", dependencies=[Depends(reject_during_impersonation("data_mutation"))])
@sensitive_limit
async def delete_sso_connection(
    request: Request,
    membership: Annotated[Membership, Depends(require_permission(MANAGE_PERMISSION))],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[None]:
    await SsoConnectionService(session, membership.org_id, redis).delete(membership.user_id)
    await session.commit()
    return Envelope(data=None, meta=Meta(request_id=_request_id(request)))


# --- the login flow ----------------------------------------------------------


@router.post(
    "/hint",
    description=(
        "Whether an email domain signs in with SSO. Answers `false` for every "
        "domain whose organization has not opted in, so this endpoint cannot "
        "be used to discover which domains have accounts here."
    ),
)
@auth_limit
async def sso_hint(
    request: Request,
    body: SsoHintRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[SsoHintResponse]:
    resolved = await SsoLoginService(session, redis).hint(body.email)
    data = (
        SsoHintResponse(
            sso_available=True,
            org_slug=resolved[0].slug,
            provider_label=resolved[1].provider_label,
        )
        if resolved is not None
        else SsoHintResponse(sso_available=False)
    )
    return Envelope(data=data, meta=Meta(request_id=_request_id(request)))


@router.get("/{org_slug}/start")
@auth_limit
async def sso_start(
    request: Request,
    org_slug: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> RedirectResponse:
    """Send the browser to the organization's IdP.

    The always-available entry point: it works whether or not the
    organization opted into domain hinting, which is what makes hinting a
    convenience rather than a requirement.
    """
    redirect_uri = str(request.url_for("sso_callback"))
    url, state = await SsoLoginService(session, redis).start(org_slug, redirect_uri=redirect_uri)

    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        key=_STATE_COOKIE_NAME,
        value=state,
        httponly=True,
        secure=True,
        # Lax, not Strict: this cookie has to ride along on the top-level GET
        # navigation the IdP issues back to /callback, which from the
        # cookie's point of view is a cross-site navigation. Strict would
        # drop it and every login would fail the state check -- the exact
        # shape of bug T-220 lost a session to.
        samesite="lax",
        max_age=STATE_TTL_SECONDS,
        path=_STATE_COOKIE_PATH,
    )
    return response


@router.get("/callback")
@auth_limit
async def sso_callback(  # noqa: PLR0913 -- code/state/session/redis on top of request/response
    request: Request,
    response: Response,
    code: str,
    state: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[LoginResponse | TwoFactorRequiredResponse]:
    cookie_state = request.cookies.get(_STATE_COOKIE_NAME)
    if cookie_state is None or cookie_state != state:
        # Login CSRF, not merely a signature check: without this an attacker
        # can complete *their* IdP login in a victim's browser and leave the
        # victim signed in as them.
        raise DomainError("errors.sso_state_mismatch", fallback="SSO state mismatch")

    result = await SsoLoginService(session, redis).complete(
        state=state,
        code=code,
        redirect_uri=str(request.url_for("sso_callback")),
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    await session.commit()
    response.delete_cookie(_STATE_COOKIE_NAME, path=_STATE_COOKIE_PATH)

    if isinstance(result, TwoFactorChallenge):
        return Envelope(
            data=TwoFactorRequiredResponse(temp_token=result.temp_token),
            meta=Meta(request_id=_request_id(request)),
        )

    access_token, refresh_plain, user = result
    _set_refresh_cookie(response, refresh_plain)
    return Envelope(
        data=LoginResponse(access_token=access_token, user=UserPublic.model_validate(user)),
        meta=Meta(request_id=_request_id(request)),
    )
