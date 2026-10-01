from time import monotonic, time
from typing import ClassVar
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID, uuid4

from fastapi import FastAPI
from limits import RateLimitItem
from limits.storage import Storage
from limits.strategies import RateLimiter, SlidingWindowCounterRateLimiter
from limits.util import WindowStats
from loguru import logger
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
# Review R5 / T-008: a sliding window, as the plan specifies, rather than
# limits' default fixed window -- which lets a caller spend a full budget in
# the last second of one window and another in the first second of the next.
# The counter variant approximates the true sliding window from two fixed
# counters, so it costs two keys per identity rather than one entry per hit.
RATE_LIMIT_STRATEGY = "sliding-window-counter"

# Seconds a limiter call may wait on Redis. The client is synchronous and runs
# on the event loop, so without a bound a Redis that stops answering (rather
# than refusing) froze every request on the worker indefinitely.
_REDIS_TIMEOUT_SECONDS = 0.25
# After a failure, how long every limit is skipped without asking Redis.
# Waiting out the timeout on every call would still turn an outage into a
# worker that serves a handful of requests a second.
_STAND_DOWN_SECONDS = 30.0


class FailOpenSlidingWindow(SlidingWindowCounterRateLimiter):
    """The one rate-limiting strategy: sliding-window-counter that fails open.

    Every limit in the app goes through it -- slowapi's endpoint limits, the
    shared user and org budgets, the per-key API budget -- so the decision
    lives here once. Failing open is deliberate: a limiter protects capacity,
    and a Redis outage has already cost capacity; refusing every request on
    top turns a degraded dependency into a total one. The outage is logged,
    once per stand-down, rather than hidden.
    """

    _down_until: ClassVar[float] = 0.0  # one Redis, so one state for all instances

    @classmethod
    def reset_stand_down(cls) -> None:
        """Test seam: forget a previous failure."""
        cls._down_until = 0.0

    def _available(self) -> bool:
        return monotonic() >= FailOpenSlidingWindow._down_until

    def _stand_down(self, exc: Exception) -> None:
        FailOpenSlidingWindow._down_until = monotonic() + _STAND_DOWN_SECONDS
        logger.warning(
            "rate_limit.storage_unavailable: limits skipped for {}s: {}", _STAND_DOWN_SECONDS, exc
        )

    def hit(self, item: RateLimitItem, *identifiers: str, cost: int = 1) -> bool:
        if not self._available():
            return True
        try:
            return super().hit(item, *identifiers, cost=cost)
        except Exception as exc:  # noqa: BLE001 -- any storage failure; see the class docstring
            self._stand_down(exc)
            return True

    def test(self, item: RateLimitItem, *identifiers: str, cost: int = 1) -> bool:
        if not self._available():
            return True
        try:
            return super().test(item, *identifiers, cost=cost)
        except Exception as exc:  # noqa: BLE001 -- as above
            self._stand_down(exc)
            return True

    def get_window_stats(self, item: RateLimitItem, *identifiers: str) -> WindowStats:
        if self._available():
            try:
                return super().get_window_stats(item, *identifiers)
            except Exception as exc:  # noqa: BLE001 -- as above
                self._stand_down(exc)
        return WindowStats(reset_time=time() + item.get_expiry(), remaining=item.amount)


def _with_timeouts(redis_url: str) -> str:
    """REDIS_URL with the limiter's socket timeouts added as query parameters,
    which redis-py parses into its connection options."""
    parts = urlsplit(redis_url)
    query = dict(parse_qsl(parts.query))
    query.setdefault("socket_timeout", str(_REDIS_TIMEOUT_SECONDS))
    query.setdefault("socket_connect_timeout", str(_REDIS_TIMEOUT_SECONDS))
    return urlunsplit(parts._replace(query=urlencode(query)))


limiter = Limiter(
    key_func=_key_func,
    storage_uri=_with_timeouts(str(_settings.REDIS_URL)),
    strategy=RATE_LIMIT_STRATEGY,
)


def strategy_for(storage: Storage) -> RateLimiter:
    """The app's strategy over `storage`. Also how tests point the shared
    limiter at a disposable Redis, so they get production's behaviour."""
    return FailOpenSlidingWindow(storage)


# slowapi builds its strategy from its own name table, which cannot name ours.
_storage = limiter._storage  # noqa: SLF001 -- see current_strategy
if not isinstance(_storage, Storage):  # the sync client: slowapi has no async path
    raise TypeError(f"unexpected rate limit storage {type(_storage).__name__}")
limiter._limiter = strategy_for(_storage)  # noqa: SLF001 -- see current_strategy


# Passing a callable (not a plain string) here matters: slowapi evaluates a
# string limit_value exactly once, at decoration time -- i.e. at import time,
# using whatever backend/.env (not backend/.env.test) contains at that
# moment, since this module loads before tests/conftest.py's autouse
# _settings_env fixture has monkeypatched anything. A callable is instead
# re-evaluated on every request, so it picks up get_settings()'s current
# (test-monkeypatched, or real) value -- this is what actually makes
# tests/test_rate_limit.py and tests/committed/test_auth_router.py's rate-limit
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
    the storage, the Redis URI, the fail-open strategy -- is shared, which
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
