"""
TOTP-based 2FA (T-024): POST /auth/2fa/enable generates a pending secret,
POST /auth/2fa/verify activates it and hands back one-time backup codes, and
POST /auth/2fa/disable requires both the current password and a valid code to
turn it back off. POST /auth/2fa/login is the second leg of a 2FA-gated
/login -- AuthService.login() (T-018) hands back a TwoFactorChallenge instead
of real tokens when totp_enabled is set, and this is where the temp_token
gets traded for one.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.dependencies import get_current_user, get_redis, invalidate_user_cache
from app.core.envelope import Envelope, Meta
from app.middleware.rate_limit import api_limit, auth_limit, sensitive_limit
from app.modules.auth.models import User
from app.modules.auth.router import _client_ip, _is_mobile_client, _request_id, _set_refresh_cookie
from app.modules.auth.schemas import (
    Disable2FARequest,
    Enable2FAResponse,
    LoginResponse,
    TwoFactorLoginRequest,
    UserPublic,
    Verify2FARequest,
    Verify2FAResponse,
)
from app.modules.auth.service import AuthService

router = APIRouter(prefix="/auth/2fa", tags=["auth"])


@router.post("/enable")
@api_limit
async def enable_2fa(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[Enable2FAResponse]:
    provisioning_uri, secret = await AuthService(session).enable_2fa(user)
    await session.commit()
    data = Enable2FAResponse(provisioning_uri=provisioning_uri, secret=secret)
    return Envelope(data=data, meta=Meta(request_id=_request_id(request)))


@router.post("/verify")
@auth_limit
async def verify_2fa(
    request: Request,
    body: Verify2FARequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[Verify2FAResponse]:
    backup_codes = await AuthService(session).verify_2fa(user, body.code)
    await session.commit()
    # T-240: flips User.totp_enabled, which require_superadmin reads off the
    # cached row to decide whether a superadmin has a second factor at all.
    await invalidate_user_cache(redis, user.id)
    return Envelope(
        data=Verify2FAResponse(backup_codes=backup_codes),
        meta=Meta(request_id=_request_id(request)),
    )


@router.post("/disable")
@sensitive_limit
async def disable_2fa(
    request: Request,
    body: Disable2FARequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[None]:
    await AuthService(session).disable_2fa(user, body.password, body.code)
    await session.commit()
    await invalidate_user_cache(redis, user.id)  # see verify_2fa above
    return Envelope(data=None, meta=Meta(request_id=_request_id(request)))


@router.post("/login")
@auth_limit
async def login_2fa(
    request: Request,
    response: Response,
    body: TwoFactorLoginRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[LoginResponse]:
    access_token, refresh_plain, user = await AuthService(session).confirm_login_2fa(
        body.temp_token, body.code, _client_ip(request), request.headers.get("user-agent", "")
    )
    await session.commit()
    _set_refresh_cookie(response, refresh_plain)
    data = LoginResponse(
        access_token=access_token,
        refresh_token=refresh_plain if _is_mobile_client(request) else None,
        user=UserPublic.model_validate(user),
    )
    return Envelope(data=data, meta=Meta(request_id=_request_id(request)))
