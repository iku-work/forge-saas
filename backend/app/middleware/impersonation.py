"""
T-142: request-scoped enrichment so every log line and audit event written
while an impersonation access token is in play carries impersonator_id,
without threading that value through every service/router call site by
hand. This middleware is NOT authentication -- a token it can't decode is
simply treated as "no impersonation" and left for
app.core.dependencies.get_current_user (and its Redis jti-blocklist check)
to accept or reject properly.
"""

from collections.abc import Awaitable, Callable

from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.audit_context import reset_impersonator_id, set_impersonator_id
from app.core.exceptions import AuthenticationError
from app.modules.auth.utils import verify_access_token

_BEARER_PREFIX = "Bearer "


class ImpersonationContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        impersonator_id = None
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith(_BEARER_PREFIX):
            try:
                payload = verify_access_token(auth_header[len(_BEARER_PREFIX) :])
            except AuthenticationError:
                payload = None
            if payload is not None:
                impersonator_id = payload.impersonator_id

        token = set_impersonator_id(impersonator_id)
        try:
            if impersonator_id is not None:
                with logger.contextualize(impersonator_id=str(impersonator_id)):
                    return await call_next(request)
            return await call_next(request)
        finally:
            reset_impersonator_id(token)
