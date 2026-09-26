from uuid import UUID, uuid4

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
from app.core.exceptions import AuthenticationError
from app.modules.auth.utils import verify_access_token


def _authenticated_user_id(request: Request) -> UUID | None:
    """The caller's user id, read from the bearer token itself.

    Not from `request.state`. This used to read `request.state.user_id`, and
    **nothing in the application ever set it** -- so the branch below was
    dead and every authenticated request was limited by IP address. Two
    people behind one office NAT shared a budget; one person on a phone and
    a laptop got two. The per-user limit §0.6 describes did not exist.

    It cannot come from a dependency either, which is why the first fix that
    suggests itself does not work: `SlowAPIMiddleware` runs during the
    middleware phase, before FastAPI has solved a single dependency, so
    anything `get_current_user` stashes arrives too late to key on. Reading
    the header here has no ordering relationship to anything.

    This is *identity for throttling only*, and deliberately tolerant: a
    token that fails to verify returns None and the caller falls back to the
    IP, exactly as an anonymous request does. Authentication itself is still
    `get_current_user`'s job, done again, properly, with the blocklist and
    session checks this has no business repeating. The duplicated signature
    verification is the cost, and it is the same RS256 verify the dependency
    does moments later.

    An API key (`fk_live_...`) is not a JWT, so it lands on the IP fallback
    here -- its own per-key budget is app.modules.api_keys.rate_limit's, and
    that one knows which key it is.
    """
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    try:
        return verify_access_token(token).user_id
    except AuthenticationError:
        return None


def _key_func(request: Request) -> str:
    user_id = _authenticated_user_id(request)
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
