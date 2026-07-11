from uuid import uuid4

from fastapi import FastAPI
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.core.envelope import Envelope, ErrorBody, Meta


def _key_func(request: Request) -> str:
    user_id = getattr(request.state, "user_id", None)
    if user_id is not None:
        return f"user:{user_id}"
    return get_remote_address(request)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid4())


def rate_limit_exceeded_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RateLimitExceeded)  # noqa: S101 — Starlette dispatches by registered exception class
    error = ErrorBody(
        code="rate_limited",
        message_key="errors.rate_limited",
        message=f"Rate limit exceeded: {exc.detail}",
    )
    envelope = Envelope[None](error=error, meta=Meta(request_id=_request_id(request)))
    return JSONResponse(status_code=429, content=envelope.model_dump(mode="json"))


_settings = get_settings()
limiter = Limiter(key_func=_key_func, storage_uri=str(_settings.REDIS_URL))

auth_limit = limiter.limit(_settings.AUTH_RATE_LIMIT)
sensitive_limit = limiter.limit(_settings.SENSITIVE_RATE_LIMIT)
api_limit = limiter.limit(_settings.API_RATE_LIMIT)


def register_rate_limiting(app: FastAPI) -> None:
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)
