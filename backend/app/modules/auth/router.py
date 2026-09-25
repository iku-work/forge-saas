"""
HTTP layer for all auth flows (T-022): registration, login, refresh,
logout(-all), email verification, password reset, magic link, and session
management. Every endpoint is a thin wrapper over AuthService -- no business
logic here per §0.1's router -> service -> repository -> model rule.

Each mutating endpoint commits its own AsyncSession explicitly: per T-005,
app.core.db.get_session() only rolls back on exception and closes in
finally -- it never commits, so the caller (here, the router) owns the
transaction boundary.
"""

from datetime import timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.dependencies import (
    get_current_user,
    get_redis,
    invalidate_user_cache,
    reject_during_impersonation,
    require_recent_auth,
)
from app.core.envelope import Envelope, Meta
from app.core.exceptions import AuthenticationError, NotFoundError
from app.middleware.rate_limit import api_limit, auth_limit, sensitive_limit
from app.models._mixins import utcnow
from app.modules.auth.models import User
from app.modules.auth.schemas import (
    AvatarUrlResponse,
    CancelEmailChangeRequest,
    ChangePasswordRequest,
    ConfirmEmailChangeRequest,
    LoginRequest,
    LoginResponse,
    MagicLinkConsumeRequest,
    MagicLinkRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    RefreshRequest,
    RefreshResponse,
    RegisterRequest,
    RequestEmailChangeRequest,
    SessionPublic,
    TwoFactorRequiredResponse,
    UpdateMeRequest,
    UserPublic,
    VerifyEmailRequest,
)
from app.modules.auth.service import AuthService, TwoFactorChallenge
from app.modules.product_settings.dependencies import reject_if_signup_disabled

router = APIRouter(prefix="/auth", tags=["auth"])

# T-216/ASVS V3.4.4: the __Host- prefix is a browser-enforced guarantee that
# this cookie was set with Secure, no Domain attribute, and Path=/ -- a
# malicious subdomain or a network attacker on plain HTTP can't get the
# browser to accept a same-named cookie that would shadow this one. The
# prefix *requires* Path=/ (a browser silently refuses to set the cookie
# otherwise), which is why the path widened from the old "/api/v1/auth" --
# httpOnly + Secure + SameSite=strict already scope who can read it.
_REFRESH_COOKIE_NAME = "__Host-refresh_token"
_REFRESH_COOKIE_PATH = "/"

# Mobile has no cookie jar, so it identifies itself with this header and
# carries its refresh token explicitly (request body in, response body
# out) instead -- see RefreshRequest/LoginResponse.refresh_token docstrings.
_PLATFORM_HEADER = "X-Client-Platform"
_MOBILE_PLATFORM = "mobile"


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid4())


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _is_mobile_client(request: Request) -> bool:
    return request.headers.get(_PLATFORM_HEADER, "").lower() == _MOBILE_PLATFORM


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=_REFRESH_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=True,
        samesite="strict",
        path=_REFRESH_COOKIE_PATH,
    )


def _bearer_refresh_token(request: Request, body: RefreshRequest | None) -> str | None:
    return request.cookies.get(_REFRESH_COOKIE_NAME) or (body.refresh_token if body else None)


# T-263: `signup_disabled` in ProductSettings closes public registration
# without a redeploy. A route-level dependency, so FastAPI solves it before it
# even validates the body -- an operator who has closed signups does not want
# the endpoint doing work, and the 403 does not depend on the payload being
# well-formed.
@router.post("/register", dependencies=[Depends(reject_if_signup_disabled)])
@auth_limit
async def register(
    request: Request,
    body: RegisterRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[None]:
    # impersonation-ok: public sign-up. Creates a *new* account from the
    # posted email; it is not an action taken on the impersonated user.
    await AuthService(session).register(body)
    await session.commit()
    return Envelope(data=None, meta=Meta(request_id=_request_id(request)))


@router.post("/login")
@auth_limit
async def login(
    request: Request,
    response: Response,
    body: LoginRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[LoginResponse] | Envelope[TwoFactorRequiredResponse]:
    # impersonation-ok: a credential exchange, not an action as anyone. It
    # mints a session for whoever passes the password check, and ignores the
    # bearer token entirely.
    result = await AuthService(session).login(
        body, _client_ip(request), request.headers.get("user-agent", "")
    )
    await session.commit()
    if isinstance(result, TwoFactorChallenge):
        return Envelope(
            data=TwoFactorRequiredResponse(temp_token=result.temp_token),
            meta=Meta(request_id=_request_id(request)),
        )

    access_token, refresh_plain, user = result
    _set_refresh_cookie(response, refresh_plain)
    data = LoginResponse(
        access_token=access_token,
        refresh_token=refresh_plain if _is_mobile_client(request) else None,
        user=UserPublic.model_validate(user),
    )
    return Envelope(data=data, meta=Meta(request_id=_request_id(request)))


@router.post("/refresh")
@api_limit
async def refresh(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    body: RefreshRequest | None = None,
) -> Envelope[RefreshResponse]:
    # impersonation-ok: reads only the refresh cookie/body field. T-142 never
    # issues a refresh token for an impersonation session, so there is nothing
    # here for one to rotate (see test_impersonation_token_cannot_be_refreshed).
    refresh_token = _bearer_refresh_token(request, body)
    if refresh_token is None:
        raise AuthenticationError(
            "errors.session_invalid",
            fallback="Your session is no longer valid. Please log in again.",
        )

    access_token, refresh_plain = await AuthService(session).refresh(
        refresh_token, _client_ip(request), request.headers.get("user-agent", "")
    )
    await session.commit()
    _set_refresh_cookie(response, refresh_plain)
    return Envelope(
        data=RefreshResponse(
            access_token=access_token,
            refresh_token=refresh_plain if _is_mobile_client(request) else None,
        ),
        meta=Meta(request_id=_request_id(request)),
    )


@router.post("/logout")
@api_limit
async def logout(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    body: RefreshRequest | None = None,
) -> Envelope[None]:
    # impersonation-ok: ends the one session named by the presented refresh
    # token -- again never the impersonation token, which has none.
    refresh_token = _bearer_refresh_token(request, body)
    if refresh_token is not None:
        await AuthService(session).logout(refresh_token)
        await session.commit()
    response.delete_cookie(_REFRESH_COOKIE_NAME, path=_REFRESH_COOKIE_PATH)
    return Envelope(data=None, meta=Meta(request_id=_request_id(request)))


@router.post("/logout-all", dependencies=[Depends(reject_during_impersonation("data_mutation"))])
@api_limit
async def logout_all(
    request: Request,
    response: Response,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[None]:
    await AuthService(session).logout_all(user.id)
    await session.commit()
    response.delete_cookie(_REFRESH_COOKIE_NAME, path=_REFRESH_COOKIE_PATH)
    return Envelope(data=None, meta=Meta(request_id=_request_id(request)))


@router.post("/verify-email")
@auth_limit
async def verify_email(
    request: Request,
    body: VerifyEmailRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[None]:
    # impersonation-ok: consumes a single-use token that only ever existed in
    # the real user's own inbox; a support agent has no way to produce one.
    verified = await AuthService(session).verify_email(body.token)
    await session.commit()
    # T-240: mark_email_verified flips is_active False -> True. A user who
    # registered, got a token, and hit an authenticated route before
    # verifying would otherwise stay locked out for the rest of the TTL.
    await invalidate_user_cache(redis, verified.id)
    return Envelope(data=None, meta=Meta(request_id=_request_id(request)))


@router.post("/forgot-password", dependencies=[Depends(reject_during_impersonation("notifying"))])
@sensitive_limit
async def forgot_password(
    request: Request,
    body: PasswordResetRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[None]:
    await AuthService(session).request_password_reset(body.email)
    await session.commit()
    return Envelope(data=None, meta=Meta(request_id=_request_id(request)))


@router.post(
    "/reset-password", dependencies=[Depends(reject_during_impersonation("data_mutation"))]
)
@sensitive_limit
async def reset_password(
    request: Request,
    body: PasswordResetConfirmRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[None]:
    reset_user = await AuthService(session).confirm_password_reset(body.token, body.new_password)
    await session.commit()
    await invalidate_user_cache(redis, reset_user.id)  # see change_password below
    return Envelope(data=None, meta=Meta(request_id=_request_id(request)))


@router.post("/magic-link", dependencies=[Depends(reject_during_impersonation("notifying"))])
@sensitive_limit
async def request_magic_link(
    request: Request,
    body: MagicLinkRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[None]:
    await AuthService(session).request_magic_link(body.email)
    await session.commit()
    return Envelope(data=None, meta=Meta(request_id=_request_id(request)))


@router.post("/magic-link/consume")
@auth_limit
async def consume_magic_link(
    request: Request,
    response: Response,
    body: MagicLinkConsumeRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[LoginResponse] | Envelope[TwoFactorRequiredResponse]:
    # impersonation-ok: the second leg of a login, gated on a token from the
    # user's inbox -- /auth/magic-link, the leg that *sends* that mail, is the
    # one that carries the block.
    result = await AuthService(session).consume_magic_link(
        body.token, _client_ip(request), request.headers.get("user-agent", "")
    )
    await session.commit()
    if isinstance(result, TwoFactorChallenge):
        # A magic link proves control of an inbox. For an account with a
        # second factor enrolled that is one factor, not two, so this
        # finishes at /auth/2fa/login like any other challenge.
        return Envelope(
            data=TwoFactorRequiredResponse(temp_token=result.temp_token),
            meta=Meta(request_id=_request_id(request)),
        )

    access_token, refresh_plain, user = result
    _set_refresh_cookie(response, refresh_plain)
    data = LoginResponse(
        access_token=access_token,
        refresh_token=refresh_plain if _is_mobile_client(request) else None,
        user=UserPublic.model_validate(user),
    )
    return Envelope(data=data, meta=Meta(request_id=_request_id(request)))


@router.get("/me")
@api_limit
async def me(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> Envelope[UserPublic]:
    return Envelope(
        data=UserPublic.model_validate(user), meta=Meta(request_id=_request_id(request))
    )


@router.patch("/me", dependencies=[Depends(reject_during_impersonation("data_mutation"))])
@api_limit
async def update_me(
    request: Request,
    body: UpdateMeRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[UserPublic]:
    # exclude_unset, not exclude_none: both fields are nullable, so an omitted
    # key and an explicit null mean different things -- leave it alone versus
    # clear it. Only what the client actually sent is written.
    changes = body.model_dump(exclude_unset=True)
    if "avatar_file_id" in changes:
        # The client names a file it uploaded; the column stores the object
        # key. Resolving here rather than trusting a key from the client is
        # what stops an arbitrary object being presigned later -- see
        # AuthService.resolve_avatar_change.
        service = AuthService(session)
        changes["avatar_object_key"] = await service.resolve_avatar_change(
            user.id, changes.pop("avatar_file_id")
        )

    if not changes:
        return Envelope(
            data=UserPublic.model_validate(user), meta=Meta(request_id=_request_id(request))
        )

    updated = await AuthService(session).update_profile(user.id, changes)
    await session.commit()
    await invalidate_user_cache(redis, user.id)
    return Envelope(
        data=UserPublic.model_validate(updated), meta=Meta(request_id=_request_id(request))
    )


@router.get("/me/avatar")
@api_limit
async def get_my_avatar(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> Envelope[AvatarUrlResponse]:
    """A short-lived presigned URL for the caller's own uploaded avatar.

    The avatars bucket is private like every other, so there is no public URL
    to put in a UserPublic -- and a presigned one does not belong there
    either, because it expires and that object is cached and re-serialised
    everywhere. Hence a separate endpoint the page calls when it renders.

    404 when the user has no uploaded avatar: an OAuth `picture` is already on
    UserPublic.avatar_url and needs nothing from here.
    """
    if user.avatar_object_key is None:
        raise NotFoundError("errors.avatar_not_found", fallback="No uploaded avatar.")

    # Lazy for the same reason as AuthService.resolve_avatar_change: this
    # module has to import with file_storage off.
    from app.modules.storage.avatars import (  # noqa: PLC0415
        AVATAR_URL_EXPIRES_IN_SECONDS,
        presign_avatar,
    )

    url = await presign_avatar(user.avatar_object_key)
    return Envelope(
        data=AvatarUrlResponse(
            url=url,
            expires_at=utcnow() + timedelta(seconds=AVATAR_URL_EXPIRES_IN_SECONDS),
        ),
        meta=Meta(request_id=_request_id(request)),
    )


@router.post(
    "/change-password", dependencies=[Depends(reject_during_impersonation("data_mutation"))]
)
@sensitive_limit
async def change_password(
    request: Request,
    body: ChangePasswordRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[None]:
    refresh_token = _bearer_refresh_token(request, None)
    await AuthService(session).change_password(
        user, body.current_password, body.new_password, refresh_token
    )
    await session.commit()
    # T-240: the cached row carries hashed_password, and this endpoint
    # verifies `current_password` against whichever copy get_current_user
    # resolved -- without this, the *old* password keeps working for a second
    # change-password call for the rest of the TTL.
    await invalidate_user_cache(redis, user.id)
    return Envelope(data=None, meta=Meta(request_id=_request_id(request)))


@router.post("/change-email", dependencies=[Depends(reject_during_impersonation("data_mutation"))])
@sensitive_limit
async def request_email_change(
    request: Request,
    body: RequestEmailChangeRequest,
    user: Annotated[User, Depends(get_current_user)],
    _recent_auth: Annotated[None, Depends(require_recent_auth(max_age_minutes=5))],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[None]:
    await AuthService(session).request_email_change(
        user,
        body.new_email,
        body.current_password,
        _client_ip(request),
        request.headers.get("user-agent", ""),
        redis,
    )
    await session.commit()
    return Envelope(data=None, meta=Meta(request_id=_request_id(request)))


@router.post(
    "/change-email/confirm", dependencies=[Depends(reject_during_impersonation("data_mutation"))]
)
@auth_limit
async def confirm_email_change(
    request: Request,
    body: ConfirmEmailChangeRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[UserPublic]:
    updated = await AuthService(session).confirm_email_change(body.token)
    await session.commit()
    await invalidate_user_cache(redis, updated.id)
    return Envelope(
        data=UserPublic.model_validate(updated), meta=Meta(request_id=_request_id(request))
    )


@router.post(
    "/change-email/cancel", dependencies=[Depends(reject_during_impersonation("data_mutation"))]
)
@auth_limit
async def cancel_email_change(
    request: Request,
    body: CancelEmailChangeRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[None]:
    # T-226: public/token-only like confirm_email_change above -- the real
    # owner clicking this from the email_change_requested notification may
    # not have (or trust) a valid session at all.
    await AuthService(session).cancel_email_change(body.token)
    await session.commit()
    return Envelope(data=None, meta=Meta(request_id=_request_id(request)))


@router.get("/sessions")
@api_limit
async def list_sessions(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[list[SessionPublic]]:
    sessions = await AuthService(session).list_sessions(user.id)
    data = [SessionPublic.model_validate(s) for s in sessions]
    return Envelope(data=data, meta=Meta(request_id=_request_id(request)))


@router.delete(
    "/sessions/{session_id}", dependencies=[Depends(reject_during_impersonation("data_mutation"))]
)
@api_limit
async def revoke_session(
    request: Request,
    session_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[None]:
    await AuthService(session).revoke_session(user.id, session_id)
    await session.commit()
    return Envelope(data=None, meta=Meta(request_id=_request_id(request)))
