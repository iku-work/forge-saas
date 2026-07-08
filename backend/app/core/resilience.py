"""
Circuit breakers on every external call (T-206): a slow or dead third party
(Stripe, Resend, S3/R2/MinIO, the virus scanner, Expo push) degrades the one
feature that depends on it -- it never cascades into request-path timeouts
or a worker pile-up.

One named `pybreaker.CircuitBreaker` per integration (`BREAKERS`, module-level
so every call site sharing an integration shares its health): `fail_max=5`
consecutive failures opens it, `reset_timeout=30` seconds before the next
call is let through as a half-open probe (one success closes it again).

pybreaker's own asyncio support (`CircuitBreaker.call_async`) is built on
`@tornado.gen.coroutine`, not a bespoke asyncio integration -- without
tornado installed it raises `NameError` the moment it's called. Tornado's
`Future` has been directly awaitable from plain asyncio code since tornado 5
(no Tornado IOLoop needed), so `await breaker.call_async(fn)` just works;
tornado is otherwise unused here and pulls in no dependencies of its own.

`resilient(integration)` is the only thing call sites use directly: it wraps
one outbound call, retries it via tenacity (exponential 0.5s -> 4s backoff,
up to `retries` attempts) but ONLY when `is_transient_error` says the
failure looks like "this third party is briefly unreachable" (a timeout, a
connection error, a de-facto 5xx) -- a business/validation error (Stripe's
InvalidRequestError, a Resend 4xx, an httpx 404) is never retried. Each
breaker's `exclude` predicate mirrors that same check: pybreaker treats an
excluded exception as a *success* for its own bookkeeping (see
`CircuitBreaker.is_system_error`'s docstring -- "business exceptions should
not cause this circuit breaker to open"), so only a transient failure that
survives every retry attempt ever counts against a breaker. Once a breaker
is open, every call raises `ExternalServiceUnavailableError` immediately,
without invoking the wrapped function (or retrying) at all.

Retry discipline (per §0.4): `resilient()` must only ever wrap a call site
that is naturally idempotent or already carries its own idempotency key --
retrying a non-idempotent call blind would double-charge a card or
double-send an email. Every current call site (Stripe reads/creates that are
themselves idempotent-by-id, Resend sends carrying their own dedupe key,
presigned-URL/HEAD/DELETE S3 operations, a scan-by-hash lookup, an Expo push
whose worst case is an occasional duplicate notification) satisfies this;
don't add a new one without checking.
"""

from __future__ import annotations

import logging
from functools import wraps
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar, cast

import botocore.exceptions
import httpx
import pybreaker
import stripe
from resend.exceptions import ResendError
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.exceptions import ExternalServiceUnavailableError
from app.core.metrics import (
    FORGE_BREAKER_OPEN_TOTAL,
    FORGE_BREAKER_STATE,
    FORGE_EXTERNAL_CALL_SECONDS,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

logger = logging.getLogger(__name__)

P = ParamSpec("P")
T = TypeVar("T")

_FAIL_MAX = 5
_RESET_TIMEOUT_SECONDS = 30
_RETRY_MULTIPLIER_SECONDS = 0.5
_RETRY_MAX_WAIT_SECONDS = 4
_HTTP_SERVER_ERROR_THRESHOLD = 500

# integration name -> what the degraded-mode 503 tells the user is affected,
# never the vendor's name (ExternalServiceUnavailableError's own contract).
_FEATURE_NAMES: dict[str, str] = {
    "stripe": "Billing",
    "resend": "Email delivery",
    "s3": "File storage",
    "virus_scanner": "File scanning",
    "expo_push": "Push notifications",
}

_STATE_VALUES = {
    pybreaker.STATE_CLOSED: 0,
    pybreaker.STATE_HALF_OPEN: 1,
    pybreaker.STATE_OPEN: 2,
}


def _is_stripe_transient(exc: BaseException) -> bool:
    # APIConnectionError: network-level. APIError: Stripe's own backend
    # misbehaved (Stripe reserves this class for its 5xx/timeout responses,
    # distinct from CardError/InvalidRequestError/RateLimitError/etc., all of
    # which are business-facing and never subclass it).
    return isinstance(exc, stripe.APIConnectionError | stripe.APIError)


def _is_resend_transient(exc: ResendError) -> bool:
    if exc.error_type == "HttpClientError":  # network failure, no HTTP response at all
        return True
    try:
        return int(exc.code) >= _HTTP_SERVER_ERROR_THRESHOLD
    except (TypeError, ValueError):
        return False


_BOTO_TRANSIENT_EXCEPTIONS = (
    botocore.exceptions.ConnectTimeoutError,
    botocore.exceptions.ReadTimeoutError,
    botocore.exceptions.EndpointConnectionError,
    botocore.exceptions.ConnectionError,
    botocore.exceptions.ConnectionClosedError,
)


def _is_boto_transient(exc: BaseException) -> bool:
    if isinstance(exc, _BOTO_TRANSIENT_EXCEPTIONS):
        return True
    if isinstance(exc, botocore.exceptions.ClientError):
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
        return cast("int", status) >= _HTTP_SERVER_ERROR_THRESHOLD
    return False


def _is_httpx_transient(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException | httpx.ConnectError | httpx.NetworkError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= _HTTP_SERVER_ERROR_THRESHOLD
    return False


def is_transient_error(exc: BaseException) -> bool:
    """Shared predicate: true only for failures that look like "the third
    party is briefly unreachable" -- never a validation/business error. Used
    both as tenacity's retry condition and (inverted) as each breaker's
    `exclude` predicate, so the two always agree on what counts."""
    # Deferred import: app.modules.storage.scanner -> app.core.config, and
    # nothing in app.core should import out to app.modules at module scope.
    from app.modules.storage.scanner import ScannerError  # noqa: PLC0415

    if isinstance(exc, ScannerError):
        return True
    if isinstance(exc, stripe.StripeError):
        return _is_stripe_transient(exc)
    if isinstance(exc, ResendError):
        return _is_resend_transient(exc)
    if isinstance(exc, (botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError)):
        return _is_boto_transient(exc)
    return _is_httpx_transient(exc)


class _MetricsListener(pybreaker.CircuitBreakerListener):
    def __init__(self, integration: str) -> None:
        self._integration = integration

    def state_change(
        self,
        cb: pybreaker.CircuitBreaker,  # noqa: ARG002 -- pybreaker's listener signature
        old_state: pybreaker.CircuitBreakerState | None,  # noqa: ARG002
        new_state: pybreaker.CircuitBreakerState,
    ) -> None:
        FORGE_BREAKER_STATE.labels(integration=self._integration).set(_STATE_VALUES[new_state.name])
        if new_state.name == pybreaker.STATE_OPEN:
            FORGE_BREAKER_OPEN_TOTAL.labels(integration=self._integration).inc()
            logger.warning("resilience.breaker_open: integration=%s", self._integration)


def _make_breaker(integration: str) -> pybreaker.CircuitBreaker:
    breaker = pybreaker.CircuitBreaker(
        fail_max=_FAIL_MAX,
        reset_timeout=_RESET_TIMEOUT_SECONDS,
        exclude=[lambda exc: not is_transient_error(exc)],
        name=integration,
        # Default (True) makes the *tripping* call itself raise
        # CircuitBreakerError instead of its own real exception -- we want
        # all 5 real failures to surface as themselves, and only a call made
        # while the breaker is *already* open to be rejected outright.
        throw_new_error_on_trip=False,
    )
    breaker.add_listener(_MetricsListener(integration))
    return breaker


BREAKERS: dict[str, pybreaker.CircuitBreaker] = {
    name: _make_breaker(name) for name in _FEATURE_NAMES
}


def reset_all_breakers() -> None:
    """Test-only seam: forces every breaker back to closed with a zeroed
    failure counter. Breakers are module-level singletons shared by every
    call site for an integration, so a test that trips one must reset it
    before the next test runs (mirrors module_loader.get_modules's own
    lru_cache.cache_clear() reset pattern)."""
    for breaker in BREAKERS.values():
        breaker.close()


def resilient(
    integration: str, *, retries: int = 3
) -> Callable[[Callable[P, Coroutine[Any, Any, T]]], Callable[P, Coroutine[Any, Any, T]]]:
    """Decorator: tenacity retry (exponential 0.5s -> 4s, retry ONLY on
    transient errors -- timeouts, connection errors, 5xx) wrapped inside the
    integration's circuit breaker. Open breaker raises
    ExternalServiceUnavailableError without attempting the call."""
    breaker = BREAKERS[integration]
    feature = _FEATURE_NAMES[integration]

    def decorator(
        func: Callable[P, Coroutine[Any, Any, T]],
    ) -> Callable[P, Coroutine[Any, Any, T]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            async def _call_with_retry() -> T:
                retryer = AsyncRetrying(
                    retry=retry_if_exception(is_transient_error),
                    wait=wait_exponential(
                        multiplier=_RETRY_MULTIPLIER_SECONDS, max=_RETRY_MAX_WAIT_SECONDS
                    ),
                    stop=stop_after_attempt(retries),
                    reraise=True,
                )
                return await retryer(func, *args, **kwargs)

            with FORGE_EXTERNAL_CALL_SECONDS.labels(integration=integration).time():
                try:
                    # pybreaker ships call_async without type annotations (its
                    # own source marks it `type: ignore[no-untyped-def]`), so
                    # its return is implicitly Any from mypy's perspective.
                    return cast("T", await breaker.call_async(_call_with_retry))  # type: ignore[no-untyped-call]
                except pybreaker.CircuitBreakerError as exc:
                    raise ExternalServiceUnavailableError(feature) from exc

        return wrapper

    return decorator
