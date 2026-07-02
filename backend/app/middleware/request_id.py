from collections.abc import Awaitable, Callable
from uuid import uuid4

import sentry_sdk
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid4())
        request.state.request_id = request_id
        sentry_sdk.set_tag("request_id", request_id)
        with logger.contextualize(request_id=request_id):
            response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
