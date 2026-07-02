"""
T-068: resolves the request's locale before any dependency or handler runs,
so a DomainError/validation-error response always has a locale to render in
-- even for requests that fail authentication or never reach a route (404s,
rate limits). Three-step priority (highest first):

1. The authenticated user's own `User.locale` -- set by
   app.core.dependencies.get_current_user once it resolves a user, since
   this codebase has no separate auth *middleware* to hook into (auth is a
   per-route FastAPI dependency, not Starlette middleware -- see that
   module's docstring). This middleware only ever supplies steps 2-3 below;
   get_current_user overwrites request.state.locale afterwards when it
   successfully resolves an authenticated user with a locale set.
2. Accept-Language header, negotiated against get_supported_locales() via
   babel.negotiate_locale.
3. get_default_locale().
"""

from collections.abc import Awaitable, Callable

from babel.core import negotiate_locale
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.i18n import get_default_locale, get_supported_locales


def _negotiate(accept_language: str | None) -> str:
    if not accept_language:
        return get_default_locale()
    preferred = [
        chunk.split(";")[0].strip() for chunk in accept_language.split(",") if chunk.strip()
    ]
    # sep="-" matches Accept-Language's hyphenated subtags (e.g. "en-US")
    # against SUPPORTED_LOCALES' bare codes (e.g. "en") -- babel's default
    # sep="_" only splits on underscores, so "en-US" would otherwise never
    # truncate down to a matchable "en".
    negotiated = negotiate_locale(preferred, get_supported_locales(), sep="-")
    return negotiated or get_default_locale()


class LocaleMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.locale = _negotiate(request.headers.get("Accept-Language"))
        return await call_next(request)


def get_locale(request: Request) -> str:
    """FastAPI dependency for routers that need the resolved locale directly."""
    locale: str | None = getattr(request.state, "locale", None)
    return locale or get_default_locale()
