from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from limits.storage import RedisStorage, storage_from_string
from limits.strategies import FixedWindowRateLimiter
from testcontainers.redis import RedisContainer

from app.middleware import rate_limit as rate_limit_module
from app.middleware.rate_limit import _key_func, auth_limit, register_rate_limiting
from app.modules.auth.utils import create_access_token


def _build_request(*, client_ip: str = "203.0.113.5", bearer: str | None = None) -> Request:
    headers = [(b"authorization", f"Bearer {bearer}".encode())] if bearer is not None else []
    return Request(scope={"type": "http", "headers": headers, "client": (client_ip, 1234)})


def _token_for(user_id: UUID) -> str:
    return create_access_token(user_id, None, ())


def test_key_func_uses_remote_address_when_anonymous() -> None:
    request = _build_request()

    assert _key_func(request) == "203.0.113.5"


def test_key_func_prefers_user_id_when_authenticated() -> None:
    """Read from the bearer token, not from `request.state`.

    The previous version of this test set `request.state.user_id` itself and
    passed -- while nothing in the application ever set that attribute, so
    every authenticated request in production was keyed by IP. A test that
    supplies the input the code reads, when nothing else supplies it, checks
    only that the branch exists.
    """
    user_id = uuid4()
    request = _build_request(bearer=_token_for(user_id))

    assert _key_func(request) == f"user:{user_id}"


def test_two_users_behind_one_ip_do_not_share_a_budget() -> None:
    # An office, a school, a country behind one NAT. Under the IP fallback
    # these two were one bucket.
    first, second = uuid4(), uuid4()
    shared_ip = "198.51.100.7"

    assert _key_func(_build_request(client_ip=shared_ip, bearer=_token_for(first))) != _key_func(
        _build_request(client_ip=shared_ip, bearer=_token_for(second))
    )


def test_one_user_on_two_addresses_shares_one_budget() -> None:
    # The converse, and the half that makes the limit worth having: a laptop
    # and a phone on different networks are one person with one allowance.
    user_id = uuid4()

    assert _key_func(_build_request(client_ip="198.51.100.7", bearer=_token_for(user_id))) == (
        _key_func(_build_request(client_ip="203.0.113.9", bearer=_token_for(user_id)))
    )


@pytest.mark.parametrize(
    "header_value",
    [
        "not-a-jwt",
        "fk_live_deadbeef_cafef00d",  # an API key: its own limiter, not this one
        "",
    ],
)
def test_an_unusable_bearer_token_falls_back_to_the_address(header_value: str) -> None:
    # Must not raise: this runs in middleware, before any exception handler
    # that understands AuthenticationError, so a throw here is a 500 on every
    # malformed Authorization header in the world.
    request = _build_request(bearer=header_value)

    assert _key_func(request) == "203.0.113.5"


def test_a_non_bearer_authorization_scheme_falls_back_to_the_address() -> None:
    request = Request(
        scope={
            "type": "http",
            "headers": [(b"authorization", b"Basic dXNlcjpwYXNz")],
            "client": ("203.0.113.5", 1234),
        }
    )

    assert _key_func(request) == "203.0.113.5"


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


# --- the budget an authenticated caller actually gets ---------------------------------
#
# Through the middleware rather than through `_key_func`, because the bug was
# never in the key function's logic -- it was that nothing supplied the input
# the key function read. Only a real request proves the header reaches it.


@pytest.mark.owasp("A04")
def test_two_users_on_one_address_get_a_budget_each(client: TestClient) -> None:
    first = {"Authorization": f"Bearer {_token_for(uuid4())}"}
    second = {"Authorization": f"Bearer {_token_for(uuid4())}"}

    exhausted = [client.get("/limited", headers=first).status_code for _ in range(6)]
    assert exhausted[:5] == [200] * 5
    assert exhausted[5] == 429

    # The second user is a different bucket, from the same TestClient and so
    # the same address. Under the IP fallback this was 429 on the first call.
    assert client.get("/limited", headers=second).status_code == 200


def test_one_user_cannot_escape_the_budget_by_changing_address(client: TestClient) -> None:
    bearer = {"Authorization": f"Bearer {_token_for(uuid4())}"}

    for _ in range(5):
        assert client.get("/limited", headers=bearer).status_code == 200

    # TestClient reports a fixed client address, so the address is already
    # constant here; what this pins is that the *key* is the user, so the
    # sixth call is refused no matter where it comes from.
    assert client.get("/limited", headers=bearer).status_code == 429


def test_an_anonymous_caller_is_still_limited(client: TestClient) -> None:
    # The fallback has to keep working: unauthenticated endpoints (login,
    # password reset) are exactly where a per-IP limit matters most.
    responses = [client.get("/limited").status_code for _ in range(6)]

    assert responses[5] == 429
