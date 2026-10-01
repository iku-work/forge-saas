"""
Social sign-in via authlib (T-023). GET /auth/oauth/{provider} signs a
10-minute state token, mirrors it in a short-lived httpOnly cookie, and
redirects to the provider. GET /auth/oauth/{provider}/callback checks the
cookie against the echoed `state` query param (CSRF protection -- this is a
classic login-CSRF defense, not just signature verification), exchanges the
code, fetches the provider's profile, and hands the result to
AuthService.oauth_login() to link-or-create a User exactly like every other
flow in this module.

State is verified with app.modules.auth.utils.sign_url_token /
verify_url_token (the same itsdangerous primitive password-reset and
magic-link tokens use) rather than authlib's session-backed CSRF state,
because this app has no Starlette SessionMiddleware -- every other cookie in
this module (see router.py's refresh-token cookie) is set explicitly per
endpoint instead.
"""

from typing import Annotated, Any, Protocol

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client
from authlib.oauth2 import OAuth2Error
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.dependencies import get_current_user, reject_during_impersonation
from app.core.envelope import Envelope, Meta
from app.core.exceptions import AuthenticationError, DomainError, NotFoundError
from app.middleware.rate_limit import api_limit, auth_limit, sensitive_limit
from app.modules.auth.models import User
from app.modules.auth.router import _client_ip, _request_id, _set_refresh_cookie
from app.modules.auth.schemas import (
    LoginResponse,
    OAuthAccountPublic,
    OAuthProfile,
    TwoFactorRequiredResponse,
    UserPublic,
)
from app.modules.auth.service import AuthService, TwoFactorChallenge
from app.modules.auth.utils import sign_url_token, verify_url_token

_STATE_COOKIE_NAME = "oauth_state"
_STATE_COOKIE_PATH = "/api/v1/auth/oauth"
_STATE_TTL_SECONDS = 600  # 10 minutes -- §T-023


class OAuthClient(Protocol):
    def authorization_url(self, *, redirect_uri: str, state: str) -> str: ...

    async def fetch_profile(self, *, code: str, redirect_uri: str) -> OAuthProfile: ...


class _AuthlibOAuthClient:
    """Shared authlib plumbing for the two providers below -- only the
    endpoint URLs, scope, and userinfo-shape differ between them.
    """

    _AUTHORIZE_URL: str
    _TOKEN_URL: str
    _SCOPE: str

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        # Test-only seam: a real client never passes this, so it always talks
        # to the real provider over the network.
        self._transport = transport

    def _new_client(self, *, redirect_uri: str) -> AsyncOAuth2Client:
        if self._transport is not None:
            return AsyncOAuth2Client(
                self._client_id,
                self._client_secret,
                scope=self._SCOPE,
                redirect_uri=redirect_uri,
                transport=self._transport,
            )
        return AsyncOAuth2Client(
            self._client_id, self._client_secret, scope=self._SCOPE, redirect_uri=redirect_uri
        )

    def authorization_url(self, *, redirect_uri: str, state: str) -> str:
        client = self._new_client(redirect_uri=redirect_uri)
        url, _ = client.create_authorization_url(self._AUTHORIZE_URL, state=state)
        return str(url)


class GoogleOAuthClient(_AuthlibOAuthClient):
    _AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    _TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 -- not a secret, an endpoint URL
    _USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
    _SCOPE = "openid email profile"

    async def fetch_profile(self, *, code: str, redirect_uri: str) -> OAuthProfile:
        async with self._new_client(redirect_uri=redirect_uri) as client:
            try:
                await client.fetch_token(
                    self._TOKEN_URL, code=code, grant_type="authorization_code"
                )
                resp = await client.get(self._USERINFO_URL)
                resp.raise_for_status()
                data: dict[str, Any] = resp.json()
                return OAuthProfile(
                    provider_user_id=str(data["sub"]),
                    email=data["email"],
                    email_verified=bool(data.get("email_verified", False)),
                    full_name=data.get("name"),
                    avatar_url=data.get("picture"),
                )
            except (httpx.HTTPError, OAuth2Error, KeyError) as exc:
                raise AuthenticationError(
                    "errors.oauth_exchange_failed",
                    fallback="Google OAuth exchange failed",
                    params={"provider": "Google"},
                ) from exc


class GitHubOAuthClient(_AuthlibOAuthClient):
    _AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
    _TOKEN_URL = "https://github.com/login/oauth/access_token"  # noqa: S105 -- endpoint URL, not a secret
    _USER_URL = "https://api.github.com/user"
    _EMAILS_URL = "https://api.github.com/user/emails"
    _SCOPE = "read:user user:email"

    async def fetch_profile(self, *, code: str, redirect_uri: str) -> OAuthProfile:
        async with self._new_client(redirect_uri=redirect_uri) as client:
            try:
                # GitHub's token endpoint replies form-encoded unless asked
                # for JSON explicitly.
                await client.fetch_token(
                    self._TOKEN_URL,
                    code=code,
                    grant_type="authorization_code",
                    headers={"Accept": "application/json"},
                )
                user_resp = await client.get(self._USER_URL)
                user_resp.raise_for_status()
                user_data: dict[str, Any] = user_resp.json()

                # /user.email is null when the user has it set to private --
                # /user/emails is the only reliable way to get a verified one.
                emails_resp = await client.get(self._EMAILS_URL)
                emails_resp.raise_for_status()
                emails_data: list[dict[str, Any]] = emails_resp.json()

                primary = next((e for e in emails_data if e.get("primary")), None) or (
                    emails_data[0] if emails_data else None
                )
                if primary is None:
                    raise AuthenticationError(
                        "errors.oauth_no_email", fallback="GitHub account has no email"
                    )

                return OAuthProfile(
                    provider_user_id=str(user_data["id"]),
                    email=primary["email"],
                    email_verified=bool(primary.get("verified", False)),
                    full_name=user_data.get("name"),
                    avatar_url=user_data.get("avatar_url"),
                )
            except (httpx.HTTPError, OAuth2Error, KeyError) as exc:
                raise AuthenticationError(
                    "errors.oauth_exchange_failed",
                    fallback="GitHub OAuth exchange failed",
                    params={"provider": "GitHub"},
                ) from exc


def get_oauth_clients() -> dict[str, OAuthClient]:
    settings = get_settings()
    clients: dict[str, OAuthClient] = {}
    if settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET:
        clients["google"] = GoogleOAuthClient(
            settings.GOOGLE_CLIENT_ID, settings.GOOGLE_CLIENT_SECRET
        )
    if settings.GITHUB_CLIENT_ID and settings.GITHUB_CLIENT_SECRET:
        clients["github"] = GitHubOAuthClient(
            settings.GITHUB_CLIENT_ID, settings.GITHUB_CLIENT_SECRET
        )
    return clients


def _get_client_or_raise(clients: dict[str, OAuthClient], provider: str) -> OAuthClient:
    client = clients.get(provider)
    if client is None:
        raise NotFoundError(
            "errors.oauth_unknown_provider",
            fallback=f"Unknown OAuth provider: {provider}",
            params={"provider": provider},
        )
    return client


router = APIRouter(prefix="/auth/oauth", tags=["auth"])


# T-216: registered before "/{provider}" -- Starlette matches path routes in
# registration order, so "/accounts" would otherwise be swallowed by
# "/{provider}" (provider="accounts") since both are GET.
@router.get("/accounts")
@api_limit
async def list_oauth_accounts(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[list[OAuthAccountPublic]]:
    accounts = await AuthService(session).list_oauth_accounts(user.id)
    data = [OAuthAccountPublic.model_validate(a) for a in accounts]
    return Envelope(data=data, meta=Meta(request_id=_request_id(request)))


@router.get("/{provider}")
@auth_limit
async def oauth_start(
    request: Request,
    provider: str,
    clients: Annotated[dict[str, OAuthClient], Depends(get_oauth_clients)],
) -> RedirectResponse:
    client = _get_client_or_raise(clients, provider)
    state = sign_url_token({"provider": provider}, _STATE_TTL_SECONDS)
    redirect_uri = str(request.url_for("oauth_callback", provider=provider))

    response = RedirectResponse(
        client.authorization_url(redirect_uri=redirect_uri, state=state), status_code=302
    )
    response.set_cookie(
        key=_STATE_COOKIE_NAME,
        value=state,
        httponly=True,
        secure=True,
        # Lax, not Strict: this cookie must still ride along on the top-level
        # GET navigation the provider issues back to our own /callback --
        # from the cookie's point of view that's a cross-site navigation.
        samesite="lax",
        max_age=_STATE_TTL_SECONDS,
        path=_STATE_COOKIE_PATH,
    )
    return response


@router.get("/{provider}/callback")
@auth_limit
async def oauth_callback(  # noqa: PLR0913 -- state/code/provider/session are each independently required
    request: Request,
    response: Response,
    provider: str,
    code: str,
    state: str,
    clients: Annotated[dict[str, OAuthClient], Depends(get_oauth_clients)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[LoginResponse] | Envelope[TwoFactorRequiredResponse]:
    client = _get_client_or_raise(clients, provider)

    cookie_state = request.cookies.get(_STATE_COOKIE_NAME)
    if cookie_state is None or cookie_state != state:
        raise DomainError("errors.oauth_state_mismatch", fallback="OAuth state mismatch")
    payload = verify_url_token(state, max_age=_STATE_TTL_SECONDS)
    if payload.get("provider") != provider:
        raise DomainError("errors.oauth_state_mismatch", fallback="OAuth state mismatch")

    redirect_uri = str(request.url_for("oauth_callback", provider=provider))
    profile = await client.fetch_profile(code=code, redirect_uri=redirect_uri)

    result = await AuthService(session).oauth_login(
        provider, profile, _client_ip(request), request.headers.get("user-agent", "")
    )
    await session.commit()

    if isinstance(result, TwoFactorChallenge):
        # A verified social identity is one factor. An account whose owner
        # enrolled a second one here gets asked for it here, whatever the
        # provider may or may not have checked upstream -- see
        # AuthService._second_factor_challenge.
        #
        # No refresh cookie is set on this path: there is no session yet, and
        # the temp token is the only thing the holder has earned.
        response.delete_cookie(_STATE_COOKIE_NAME, path=_STATE_COOKIE_PATH)
        return Envelope(
            data=TwoFactorRequiredResponse(temp_token=result.temp_token),
            meta=Meta(request_id=_request_id(request)),
        )

    access_token, refresh_plain, user = result
    _set_refresh_cookie(response, refresh_plain)
    response.delete_cookie(_STATE_COOKIE_NAME, path=_STATE_COOKIE_PATH)
    data = LoginResponse(access_token=access_token, user=UserPublic.model_validate(user))
    return Envelope(data=data, meta=Meta(request_id=_request_id(request)))


@router.post(
    "/{provider}/unlink", dependencies=[Depends(reject_during_impersonation("data_mutation"))]
)
@sensitive_limit
async def oauth_unlink(
    request: Request,
    provider: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Envelope[None]:
    await AuthService(session).unlink_oauth(user, provider)
    await session.commit()
    return Envelope(data=None, meta=Meta(request_id=_request_id(request)))
