from uuid import uuid4

from fastapi import FastAPI
from limits.strategies import RateLimiter
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

# Passing a callable (not a plain string) here matters: slowapi evaluates a
# string limit_value exactly once, at decoration time -- i.e. at import time,
# using whatever backend/.env (not backend/.env.test) contains at that
# moment, since this module loads before tests/conftest.py's autouse
# _settings_env fixture has monkeypatched anything. A callable is instead
# re-evaluated on every request, so it picks up get_settings()'s current
# (test-monkeypatched, or real) value -- this is what actually makes
# tests/test_rate_limit.py and tests/test_auth_router.py's rate-limit
# assertions correct regardless of what AUTH_RATE_LIMIT a developer's own
# backend/.env happens to set (T-220 raised it there for local E2E use,
# which silently broke every "Nth call gets 429" test on that machine until
# this fix, since the static-string version baked 1000/minute in at import
# time no matter what .env.test said).
auth_limit = limiter.limit(lambda: get_settings().AUTH_RATE_LIMIT)
sensitive_limit = limiter.limit(lambda: get_settings().SENSITIVE_RATE_LIMIT)
api_limit = limiter.limit(lambda: get_settings().API_RATE_LIMIT)


def current_strategy() -> RateLimiter:
    """The limiter's configured strategy, as one accessor rather than N
    reaches into a private attribute.

    T-312 needs the strategy itself, not the decorator: a per-key budget is
    consumed inside a dependency that already knows which key it is, so it
    calls `hit()`/`get_window_stats()` directly instead of asking slowapi to
    derive an identity from the request. Everything else about the limiter --
    the storage, the Redis URI, the fixed-window strategy -- is shared, which
    is also what makes tests/conftest.py's `_rate_limit_storage` fixture (it
    swaps `_storage` and `_limiter` on this singleton) cover both kinds of
    limit with one swap.

    The attribute is private in slowapi and there is no public alternative;
    reading it in exactly one place is the containment.
    """
    strategy: RateLimiter = limiter._limiter  # noqa: SLF001 -- see the docstring
    return strategy


def register_rate_limiting(app: FastAPI) -> None:
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)
