"""
WebAuthn/FIDO2 second factor (T-217): POST /auth/webauthn/register/options
begins registering a hardware key or platform authenticator for the current
user via navigator.credentials.create(), and POST /auth/webauthn/register
completes it. GET/DELETE /auth/webauthn/credentials manage what's registered.

POST /auth/webauthn/login/options and POST /auth/webauthn/login are the
WebAuthn counterpart to app.modules.auth.totp's /2fa/login -- AuthService.
login() (T-018) hands back the same TwoFactorChallenge whether the user
enrolled TOTP or a security key; whichever ceremony the client completes
trades that temp_token for real tokens.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.dependencies import get_current_user
from app.core.envelope import Envelope, Meta
from app.middleware.rate_limit import api_limit, auth_limit, sensitive_limit
from app.modules.auth.models import User
from app.modules.auth.router import (
    _client_ip,
    _is_mobile_client,
    _request_id,
    _set_refresh_cookie,
)
from app.modules.auth.schemas import (
    LoginResponse,
    UserPublic,
    WebAuthnCredentialPublic,
    WebAuthnLoginOptionsRequest,
    WebAuthnLoginOptionsResponse,
    WebAuthnLoginRequest,
    WebAuthnRegisterOptionsResponse,
    WebAuthnRegisterRequest,
)
from app.modules.auth.service import AuthService

router = APIRouter(prefix="/auth/webauthn", tags=["auth"])


@router.post("/register/options")
@api_limit
async def register_options(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[WebAuthnRegisterOptionsResponse]:
    options, challenge_token = await AuthService(session).begin_webauthn_registration(user)
    return Envelope(
        data=WebAuthnRegisterOptionsResponse(options=options, challenge_token=challenge_token),
        meta=Meta(request_id=_request_id(request)),
    )


@router.post("/register")
@sensitive_limit
async def register(
    request: Request,
    body: WebAuthnRegisterRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[WebAuthnCredentialPublic]:
    record = await AuthService(session).complete_webauthn_registration(
        user, body.challenge_token, body.credential, body.nickname
    )
    await session.commit()
    return Envelope(
        data=WebAuthnCredentialPublic.model_validate(record),
        meta=Meta(request_id=_request_id(request)),
    )


@router.get("/credentials")
@api_limit
async def list_credentials(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[list[WebAuthnCredentialPublic]]:
    records = await AuthService(session).list_webauthn_credentials(user.id)
    data = [WebAuthnCredentialPublic.model_validate(r) for r in records]
    return Envelope(data=data, meta=Meta(request_id=_request_id(request)))


@router.delete("/credentials/{credential_id}")
@api_limit
async def revoke_credential(
    request: Request,
    credential_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[None]:
    await AuthService(session).revoke_webauthn_credential(user.id, credential_id)
    await session.commit()
    return Envelope(data=None, meta=Meta(request_id=_request_id(request)))


@router.post("/login/options")
@auth_limit
async def login_options(
    request: Request,
    body: WebAuthnLoginOptionsRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[WebAuthnLoginOptionsResponse]:
    options, challenge_token = await AuthService(session).begin_webauthn_login(body.temp_token)
    return Envelope(
        data=WebAuthnLoginOptionsResponse(options=options, challenge_token=challenge_token),
        meta=Meta(request_id=_request_id(request)),
    )


@router.post("/login")
@auth_limit
async def login(
    request: Request,
    response: Response,
    body: WebAuthnLoginRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[LoginResponse]:
    access_token, refresh_plain, user = await AuthService(session).complete_webauthn_login(
        body.challenge_token,
        body.credential,
        _client_ip(request),
        request.headers.get("user-agent", ""),
    )
    await session.commit()
    _set_refresh_cookie(response, refresh_plain)
    data = LoginResponse(
        access_token=access_token,
        refresh_token=refresh_plain if _is_mobile_client(request) else None,
        user=UserPublic.model_validate(user),
    )
    return Envelope(data=data, meta=Meta(request_id=_request_id(request)))
