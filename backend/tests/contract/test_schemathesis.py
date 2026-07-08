"""
T-050: fuzz every `/api/v1/...` (and `/health/...`) operation in the app's
own OpenAPI schema for response-schema conformance and unexpected 500s.

Why requests are NOT sent via `case.call()` / schemathesis's own ASGI
transport: schemathesis (v4) drives ASGI calls through
`starlette_testclient.TestClient`, and `ASGITransport.send()` enters a brand
new `TestClient(app)` context -- meaning a fresh background thread, a fresh
anyio event loop, and a full app lifespan start/stop -- for *every single*
generated case. That is incompatible with this app's `get_session`/
`get_redis` overrides: the root conftest's `session`/`_redis_client`
fixtures hold an asyncpg connection and a redis-asyncio client bound to
pytest-asyncio's one loop, and asyncpg connections raise if awaited from a
different loop than the one that created them.

Instead, `schemathesis.openapi.from_asgi` is used purely to discover the
schema (a single, lifespan-free `GET /openapi.json` -- see
`schemathesis/python/asgi.py::get_client`, which only enters `TestClient`'s
`__enter__`/lifespan when used as a context manager, not for a bare
`client.get(...)`). Each generated `case` is then turned into kwargs via
`case.as_transport_kwargs()` and sent through the *existing*
`authed_client` fixture (root conftest.py) -- the same real-Postgres/
real-Redis, single-event-loop httpx client every other test file uses --
and the response is fed back to `case.validate_response()` for checking.
This also means each operation's many fuzzed examples share one
transaction-per-test (rolled back after, per the `session` fixture), so the
shared testcontainer Postgres is never polluted.

Checks are deliberately narrowed to `not_a_server_error` +
`response_schema_conformance` (skip cleanly on undocumented status codes --
see `ResponseValidator.validate`'s `find_by_status_code` short-circuit).
`status_code_conformance` is excluded: this app (per §0.4) never documents
401/403/404/409/429 responses in the OpenAPI schema (only the success shape
and FastAPI's auto-added 422 are), so it would fail on literally every
non-2xx response regardless of correctness.

Auth: every generated case gets the real, currently-authed user's bearer
token and `X-Org-ID` stamped on via a schema-scoped `map_headers` hook
(rather than `client.headers` defaults), because secured operations also
generate their *own* random `Authorization`/`X-Org-ID` values as part of
normal fuzzing -- a hook is the one place guaranteed to run last and win.
Stripe's `Customer.create`/`checkout.Session.create`/
`billing_portal.Session.create` are monkeypatched (same fakes as
test_billing_checkout.py) so a fuzzed-but-authorized hit on
/billing/{checkout,portal}-session can never reach the real Stripe API.
OAuth (no GOOGLE_CLIENT_ID/GITHUB_CLIENT_ID in .env.test) and Resend
(stubbed at the service layer) are already inert by default.
"""

from collections.abc import AsyncGenerator, Generator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import schemathesis
import stripe
from limits.storage import RedisStorage
from schemathesis.config import HealthCheck
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app, check_database_ready, check_redis_ready

schema = schemathesis.openapi.from_asgi("/openapi.json", app)
# GET /notifications/stream (T-103) is a long-lived SSE response that never
# completes on its own -- fundamentally incompatible with this file's
# `authed_client.request(**kwargs)` round trip (see module docstring above):
# httpx's ASGITransport only returns a Response after the whole ASGI call
# finishes, so a fuzzed hit on this operation hangs forever rather than
# producing a response to validate. Excluded rather than special-cased, the
# same way this file already treats Stripe/OAuth/Resend as out of scope for
# generic contract fuzzing.
schema = schema.exclude(path="/api/v1/notifications/stream")
schema.config.generation.max_examples = 20
# Every example for one operation deliberately reuses that operation's one
# `authed_client`/`session` (see module docstring) -- a real Postgres
# transaction, rolled back once per operation rather than per example, is
# the whole reason this test never pollutes the shared testcontainer DB.
# Hypothesis's "function_scoped_fixture" health check would otherwise flag
# exactly that as suspicious; schemathesis.config.HealthCheck doesn't expose
# it individually (only via `all`), unlike hypothesis's own enum.
schema.config.update(suppress_health_check=[HealthCheck.all])

schemathesis.checks.load_all_checks()
_CHECKS = schemathesis.checks.CHECKS.get_by_names(
    ["not_a_server_error", "response_schema_conformance"]
)

pytestmark = pytest.mark.owasp("A03")


@dataclass
class _AuthContext:
    authorization: str | None = None
    x_org_id: str | None = None


_auth_ctx = _AuthContext()


@schema.hooks.hook
def map_headers(
    context: schemathesis.HookContext,  # noqa: ARG001 -- required hook signature
    headers: dict[str, str] | None,
) -> dict[str, str]:
    headers = dict(headers or {})
    if _auth_ctx.authorization is not None:
        headers["Authorization"] = _auth_ctx.authorization
    if _auth_ctx.x_org_id is not None:
        headers["X-Org-ID"] = _auth_ctx.x_org_id
    return headers


@pytest.fixture(autouse=True)
async def _inject_auth(authed_client: httpx.AsyncClient) -> AsyncGenerator[None, None]:
    _auth_ctx.authorization = authed_client.headers["Authorization"]
    _auth_ctx.x_org_id = authed_client.headers["X-Org-ID"]
    yield
    _auth_ctx.authorization = None
    _auth_ctx.x_org_id = None


class _FakeStripeObject:
    def __init__(self, **attrs: str) -> None:
        for key, value in attrs.items():
            setattr(self, key, value)


@pytest.fixture(autouse=True)
def _stub_health_dependencies() -> Generator[None, None, None]:
    # /health/ready's app.state.engine/app.state.redis (set in app.main's
    # lifespan) point at production settings, not the testcontainers this
    # file's fixtures spin up -- see test_health.py for the same override.
    # Without it this operation always 503s here, which is a test-harness
    # gap, not a contract bug T-050 is meant to surface.
    app.dependency_overrides[check_database_ready] = lambda: True
    app.dependency_overrides[check_redis_ready] = lambda: True
    yield
    # .pop(..., None), not del: the `client` fixture's own teardown may have
    # already cleared `app.dependency_overrides` wholesale by the time this
    # runs, depending on fixture teardown order.
    app.dependency_overrides.pop(check_database_ready, None)
    app.dependency_overrides.pop(check_redis_ready, None)


@pytest.fixture(autouse=True)
def _stub_stripe(monkeypatch: pytest.MonkeyPatch) -> None:
    # T-206 switched app.modules.billing.service onto stripe's `_async`
    # method variants (so they can be wrapped in app.core.resilience's
    # async @resilient("stripe")) -- stub those, not the sync originals.
    async def _customer_create(**_kwargs: object) -> _FakeStripeObject:
        return _FakeStripeObject(id="cus_contract_test")

    async def _checkout_create(**_kwargs: object) -> _FakeStripeObject:
        return _FakeStripeObject(url="https://checkout.stripe.com/contract-test")

    async def _portal_create(**_kwargs: object) -> _FakeStripeObject:
        return _FakeStripeObject(url="https://billing.stripe.com/contract-test")

    monkeypatch.setattr(stripe.Customer, "create_async", _customer_create)
    monkeypatch.setattr(stripe.checkout.Session, "create_async", _checkout_create)
    monkeypatch.setattr(stripe.billing_portal.Session, "create_async", _portal_create)


@schema.parametrize()
async def test_api_contract(
    case: schemathesis.Case,
    authed_client: httpx.AsyncClient,
    session: AsyncSession,
    _rate_limit_storage: RedisStorage,  # noqa: PT019 -- conftest names it this; its return value is used below
) -> None:
    # Give every operation its own full rate-limit budget -- §0.6's
    # 5-100/minute limits exist to be tested by test_rate_limit.py, not to
    # silently truncate how many of Hypothesis's examples actually reach
    # this app's business logic.
    _rate_limit_storage.reset()

    kwargs: dict[str, Any] = case.as_transport_kwargs()
    if not kwargs.get("cookies"):
        kwargs.pop("cookies", None)
    # schemathesis builds `requests`-style kwargs; httpx accepts a raw
    # str/bytes `data=` too but warns to prefer `content=` (e.g. the
    # coverage phase's deliberately-malformed-JSON-body cases land here).
    if isinstance(kwargs.get("data"), str | bytes):
        kwargs["content"] = kwargs.pop("data")
    # Negative-mode header fuzzing deliberately tries non-ASCII bytes (RFC
    # 7230 says header values are Latin-1) to see how the app handles a
    # malformed header. `requests` (schemathesis's own transport) tolerates
    # that; httpx's str-header encoding defaults to strict ascii and raises
    # client-side before a request is even sent. Pre-encoding as UTF-8 bytes
    # keeps the malformed-value intent while letting httpx transmit it.
    headers = kwargs.get("headers")
    if headers:
        kwargs["headers"] = {
            key: value.encode("utf-8") if isinstance(value, str) and not value.isascii() else value
            for key, value in headers.items()
        }

    try:
        response = await authed_client.request(**kwargs)
    except Exception:
        # httpx's ASGITransport defaults to re-raising any exception that
        # reaches Starlette's ServerErrorMiddleware instead of returning the
        # 500 response it already sent on the wire (see
        # test_handlers.py::test_unhandled_exception_returns_500_with_no_traceback,
        # which needs `raise_server_exceptions=False` to observe that 500
        # directly) -- so an unhandled exception here *is* this app's 500.
        # Roll back before re-raising: every example for this operation
        # shares one session (see module docstring), and Postgres leaves a
        # transaction unusable after an error until it's rolled back --
        # without this, one bad example poisons every example after it,
        # including Hypothesis's own shrinking attempts.
        await session.rollback()
        raise

    case.validate_response(response, checks=_CHECKS)
