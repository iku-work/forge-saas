from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": "default-src 'self'",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        # T-210: ZAP's "Storable and Cacheable Content" check flags any
        # response (404s included) with no explicit caching directive as a
        # potential sensitive-data-caching risk. setdefault, not update --
        # api/v1/config (T-063) deliberately sets its own long-lived public
        # Cache-Control and must keep it.
        response.headers.setdefault("Cache-Control", "no-store")
        return response
