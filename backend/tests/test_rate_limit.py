from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from limits.storage import RedisStorage, storage_from_string
from limits.strategies import FixedWindowRateLimiter
from testcontainers.redis import RedisContainer

from app.middleware import rate_limit as rate_limit_module
from app.middleware.rate_limit import _key_func, auth_limit, register_rate_limiting


def _build_request(*, client_ip: str = "203.0.113.5") -> Request:
    return Request(scope={"type": "http", "headers": [], "client": (client_ip, 1234)})


def test_key_func_uses_remote_address_when_anonymous() -> None:
    request = _build_request()

    assert _key_func(request) == "203.0.113.5"


def test_key_func_prefers_user_id_when_authenticated() -> None:
    request = _build_request()
    request.state.user_id = "user-123"

    assert _key_func(request) == "user:user-123"


_test_app = FastAPI()
register_rate_limiting(_test_app)


@_test_app.get("/limited")
@auth_limit
async def _limited_endpoint(request: Request) -> dict[str, str]:  # noqa: ARG001 — slowapi requires a literal `request` param to bind the limit
    return {"status": "ok"}


@pytest.fixture(scope="module")
def _redis_backed_storage() -> Iterator[RedisStorage]:
    # The module-level `limiter` is built once at import time against whatever
    # REDIS_URL is in the environment. Point it at a real, disposable Redis
    # for this test module, then restore the original storage on teardown so
    # other test modules sharing the same `limiter` singleton are unaffected.
    original_storage = rate_limit_module.limiter._storage
    original_limiter = rate_limit_module.limiter._limiter
    with RedisContainer("redis:7-alpine") as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6379)
        storage = storage_from_string(f"redis://{host}:{port}/0")
        rate_limit_module.limiter._storage = storage
        rate_limit_module.limiter._limiter = FixedWindowRateLimiter(storage)
        yield storage
    rate_limit_module.limiter._storage = original_storage
    rate_limit_module.limiter._limiter = original_limiter


@pytest.fixture
def client(_redis_backed_storage: RedisStorage) -> TestClient:
    _redis_backed_storage.reset()
    return TestClient(_test_app)


@pytest.mark.owasp("A04")
def test_sixth_call_within_a_minute_is_rate_limited(client: TestClient) -> None:
    responses = [client.get("/limited") for _ in range(6)]

    assert [r.status_code for r in responses[:5]] == [200] * 5
    assert responses[5].status_code == 429


def test_rate_limit_response_uses_standard_envelope(client: TestClient) -> None:
    for _ in range(5):
        client.get("/limited")

    response = client.get("/limited")

    assert response.status_code == 429
    body = response.json()
    assert body["data"] is None
    assert body["error"]["code"] == "rate_limited"
    assert body["meta"]["request_id"]
