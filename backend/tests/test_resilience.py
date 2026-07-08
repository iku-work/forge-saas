"""
Verifies T-206's app.core.resilience directly: the breaker/retry mechanics
themselves, independent of any one integration's business logic (each call
site's own degraded-mode behavior -- re-queue vs. drop vs. 503 -- is covered
by that call site's own test file: test_email_job.py, test_virus_scan_job.py,
test_notification_service.py, test_storage_backend.py,
test_billing_checkout.py, test_billing_invoices_service.py).

Uses the real "expo_push" breaker (fail_max=5, reset_timeout=30s) rather than
a throwaway one -- BREAKERS is a fixed, module-level registry keyed by the
five real integration names (mirroring how module_loader.get_modules's
lru_cache is a real, shared singleton in its own tests). conftest.py's
autouse `_reset_breakers` fixture resets every breaker around each test so
failures/opens never leak across tests sharing this module-level state.
"""

from typing import Any

import httpx
import pytest
from freezegun import freeze_time

from app.core.exceptions import ExternalServiceUnavailableError
from app.core.resilience import BREAKERS, is_transient_error, resilient

_INTEGRATION = "expo_push"


def _make_flaky_call(exc: BaseException) -> tuple[Any, list[int]]:
    calls: list[int] = []

    @resilient(_INTEGRATION, retries=1)
    async def _flaky() -> None:
        calls.append(1)
        raise exc

    return _flaky, calls


async def test_breaker_opens_after_five_consecutive_failures_and_stops_invoking_the_call() -> None:
    flaky, calls = _make_flaky_call(httpx.ConnectError("boom"))

    for _ in range(5):
        with pytest.raises(httpx.ConnectError):
            await flaky()
    assert len(calls) == 5
    assert BREAKERS[_INTEGRATION].current_state == "open"

    with pytest.raises(ExternalServiceUnavailableError):
        await flaky()

    # The 6th call never invoked the wrapped function at all.
    assert len(calls) == 5


async def test_external_service_unavailable_names_the_feature_not_the_vendor() -> None:
    flaky, _calls = _make_flaky_call(httpx.ConnectError("boom"))
    for _ in range(5):
        with pytest.raises(httpx.ConnectError):
            await flaky()

    with pytest.raises(ExternalServiceUnavailableError) as exc_info:
        await flaky()

    assert "Push notifications" in exc_info.value.fallback
    assert "expo" not in exc_info.value.fallback.lower()


async def test_breaker_half_open_after_reset_timeout_and_one_success_closes_it() -> None:
    flaky, _calls = _make_flaky_call(httpx.ConnectError("boom"))

    with freeze_time("2026-01-01 00:00:00") as frozen:
        for _ in range(5):
            with pytest.raises(httpx.ConnectError):
                await flaky()
        assert BREAKERS[_INTEGRATION].current_state == "open"

        frozen.move_to("2026-01-01 00:00:31")  # past the 30s reset_timeout

        @resilient(_INTEGRATION, retries=1)
        async def _ok() -> str:
            return "ok"

        result = await _ok()
        assert result == "ok"
        assert BREAKERS[_INTEGRATION].current_state == "closed"


async def test_retry_fires_on_timeout_but_not_on_a_400_response() -> None:
    timeout_attempts = 0

    @resilient(_INTEGRATION, retries=3)
    async def _times_out_once() -> str:
        nonlocal timeout_attempts
        timeout_attempts += 1
        if timeout_attempts < 2:
            raise httpx.TimeoutException("slow")
        return "recovered"

    assert await _times_out_once() == "recovered"
    assert timeout_attempts == 2  # first attempt timed out, retried once, then succeeded

    bad_request_attempts = 0

    @resilient(_INTEGRATION, retries=3)
    async def _bad_request() -> None:
        nonlocal bad_request_attempts
        bad_request_attempts += 1
        response = httpx.Response(400, request=httpx.Request("GET", "https://example.test"))
        raise httpx.HTTPStatusError("bad request", request=response.request, response=response)

    with pytest.raises(httpx.HTTPStatusError):
        await _bad_request()
    assert bad_request_attempts == 1  # never retried

    # A 400 also never counts as a breaker failure (it's excluded / a
    # business error), so the breaker is still closed afterward.
    assert BREAKERS[_INTEGRATION].current_state == "closed"


def test_is_transient_error_treats_5xx_as_transient_and_4xx_as_not() -> None:
    request = httpx.Request("GET", "https://example.test")
    server_error = httpx.HTTPStatusError(
        "boom", request=request, response=httpx.Response(503, request=request)
    )
    client_error = httpx.HTTPStatusError(
        "bad", request=request, response=httpx.Response(404, request=request)
    )

    assert is_transient_error(server_error) is True
    assert is_transient_error(client_error) is False
    assert is_transient_error(httpx.ConnectError("boom")) is True
    assert is_transient_error(httpx.TimeoutException("boom")) is True
    assert is_transient_error(ValueError("not a network error at all")) is False
