from collections.abc import Awaitable, Callable
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.envelope import Envelope, ErrorBody, Meta
from app.core.i18n import get_default_locale, t

SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": "default-src 'self'",
}

# T-225/ASVS V13.1.5, V13.2.5: every mutating endpoint in this API is
# JSON-only (file uploads go client -> storage backend directly via a
# presigned POST, never through the backend itself -- see
# app.modules.storage.service's module docstring), so a non-JSON body on one
# of these methods is always either a client bug or a probe.
_JSON_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})


def _has_disallowed_content_type(request: Request) -> bool:
    if request.method not in _JSON_BODY_METHODS:
        return False
    content_length = request.headers.get("content-length")
    if not content_length or content_length == "0":
        return False
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    return media_type != "application/json"


def _unsupported_media_type_response(request: Request) -> JSONResponse:
    locale: str | None = getattr(request.state, "locale", None) or get_default_locale()
    request_id: str = getattr(request.state, "request_id", None) or str(uuid4())
    message_key = "errors.unsupported_media_type"
    try:
        message = t(message_key, locale)
    except KeyError:
        message = "Content-Type must be application/json."
    error = ErrorBody(code="unsupported_media_type", message_key=message_key, message=message)
    envelope = Envelope[None](error=error, meta=Meta(request_id=request_id))
    return JSONResponse(status_code=415, content=envelope.model_dump(mode="json"))


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # A pre-check, not a raised exception: this middleware sits between
        # the router's ExceptionMiddleware (inside) and every other
        # user-added middleware (outside) in Starlette's stack, so an
        # exception raised here -- rather than from inside call_next --
        # would skip the DomainError handler entirely and surface as a bare
        # 500. Building the envelope response directly sidesteps that.
        response: Response
        if _has_disallowed_content_type(request):
            response = _unsupported_media_type_response(request)
        else:
            response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        # T-210: ZAP's "Storable and Cacheable Content" check flags any
        # response (404s included) with no explicit caching directive as a
        # potential sensitive-data-caching risk. setdefault, not update --
        # api/v1/config (T-063) deliberately sets its own long-lived public
        # Cache-Control and must keep it.
        response.headers.setdefault("Cache-Control", "no-store")
        # T-225/ASVS V14.4.2: every /api/v1/* JSON response is marked
        # non-renderable. Scoped to the API prefix, not every response --
        # /docs and /redoc (dev only, see app.main) are meant to render as
        # HTML, and forcing "attachment" on them would make the browser
        # download the page instead.
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Content-Disposition", "attachment")
        return response
