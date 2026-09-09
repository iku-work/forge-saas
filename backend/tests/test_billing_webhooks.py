"""
Verifies T-030 (Stripe webhook handler): the same event delivered twice is
processed exactly once, a bad signature is rejected before any WebhookEvent
row is written, and a handler exception leaves the row in a retryable state
(error set, processed_at still null) while returning 500 so Stripe redelivers.

Builds real Stripe-shaped signatures with the documented HMAC scheme (see
stripe._webhook.WebhookSignature._compute_signature) against the
STRIPE_WEBHOOK_SECRET in .env.test, rather than mocking stripe.Webhook.
construct_event -- this exercises the real signature verification path the
same way Stripe's own delivery would.

Same real-Postgres testcontainers + httpx.AsyncClient(ASGITransport(...))
pattern as test_auth_router.py -- see that file's docstring for why
TestClient is avoided. No Redis container: this route carries no rate-limit
decorator, so slowapi's middleware never touches its storage (confirmed by
reading slowapi.middleware._check_limits -- with no per-route or default
limits registered, it short-circuits without a storage call).
"""

import hashlib
import hmac
import json
import time
from collections.abc import AsyncGenerator, Callable
from typing import Any
from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport
from prometheus_client import REGISTRY
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel
from testcontainers.postgres import PostgresContainer

from app.core.config import get_settings
from app.core.db import get_session
from app.main import app
from app.modules.billing.models import Plan, Subscription, WebhookEvent
from app.modules.billing.repository import WebhookEventRepository
from app.modules.billing.service import BillingService

# T-291: this file needs billing enabled -- its routes, its tables, or
# both. CI's module-matrix job deselects it in the combination that turns
# every optional module off.
pytestmark = pytest.mark.module_dependent

_TABLES = [Plan.__table__, Subscription.__table__, WebhookEvent.__table__]


@pytest.fixture(scope="module")
def postgres_container() -> "PostgresContainer":
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


# Function-scoped (not module-scoped): see test_auth_repository.py -- an
# asyncpg-backed engine can't be reused across pytest-asyncio's per-test loop.
@pytest.fixture
async def test_engine(postgres_container: "PostgresContainer") -> AsyncGenerator[AsyncEngine, None]:
    url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all, tables=_TABLES)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all, tables=_TABLES)
    await engine.dispose()


@pytest.fixture
def session_factory(test_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


# A direct handle for test setup/assertions -- separate from the sessions the
# overridden get_session() hands to each request, exactly like a second
# psql connection would be in production.
@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as s:
        yield s


@pytest.fixture
def get_session_override(
    session_factory: async_sessionmaker[AsyncSession],
) -> Callable[[], AsyncGenerator[AsyncSession, None]]:
    async def _get_session() -> AsyncGenerator[AsyncSession, None]:
        request_session = session_factory()
        try:
            yield request_session
        except Exception:
            await request_session.rollback()
            raise
        finally:
            await request_session.close()

    return _get_session


@pytest.fixture
async def client(
    get_session_override: Callable[[], AsyncGenerator[AsyncSession, None]],
) -> AsyncGenerator[httpx.AsyncClient, None]:
    app.dependency_overrides[get_session] = get_session_override
    try:
        async with app.router.lifespan_context(app):
            # raise_app_exceptions=False: ServerErrorMiddleware sends the
            # registered Exception handler's 500 response *and then*
            # re-raises for visibility in dev -- needed here since
            # test_handler_exception_records_error_and_returns_500 below
            # deliberately exercises that path and wants the Response, not
            # the re-raised exception (see test_handlers.py's TestClient
            # equivalent: raise_server_exceptions=False).
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as c:
                yield c
    finally:
        app.dependency_overrides.clear()


def _stripe_event(
    *, event_id: str, event_type: str = "checkout.session.completed"
) -> dict[str, Any]:
    # A type with no handler in BillingService.handle_event's dispatch --
    # these tests exercise the webhook/idempotency plumbing, not any
    # particular business outcome, so a no-op type keeps setup minimal.
    return {
        "id": event_id,
        "object": "event",
        "type": event_type,
        "data": {"object": {"id": "obj_1"}},
    }


def _sign(payload: bytes, secret: str, *, timestamp: int | None = None) -> str:
    ts = timestamp if timestamp is not None else int(time.time())
    signed_payload = f"{ts}.{payload.decode('utf-8')}"
    signature = hmac.new(
        secret.encode("utf-8"), signed_payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"t={ts},v1={signature}"


async def _post_webhook(
    client: httpx.AsyncClient, body: dict[str, Any], *, secret: str | None = None
) -> httpx.Response:
    payload = json.dumps(body).encode("utf-8")
    signing_secret = secret if secret is not None else get_settings().STRIPE_WEBHOOK_SECRET
    headers = {
        "stripe-signature": _sign(payload, signing_secret),
        "content-type": "application/json",
    }
    return await client.post("/api/v1/billing/webhooks/stripe", content=payload, headers=headers)


# --- signature verification -------------------------------------------------------


@pytest.mark.owasp("A08")
async def test_bad_signature_returns_400_and_creates_no_row(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    event_id = f"evt_{uuid4().hex}"
    response = await _post_webhook(client, _stripe_event(event_id=event_id), secret="whsec_wrong")

    assert response.status_code == 400
    repo = WebhookEventRepository(session)
    assert await repo.get_by_id(event_id) is None


async def test_missing_signature_header_returns_400(client: httpx.AsyncClient) -> None:
    payload = json.dumps(_stripe_event(event_id=f"evt_{uuid4().hex}")).encode("utf-8")
    response = await client.post(
        "/api/v1/billing/webhooks/stripe",
        content=payload,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400


# --- idempotency --------------------------------------------------------------------


async def test_first_delivery_is_processed_and_marked_processed(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    event_id = f"evt_{uuid4().hex}"

    response = await _post_webhook(client, _stripe_event(event_id=event_id))

    assert response.status_code == 200
    repo = WebhookEventRepository(session)
    stored = await repo.get_by_id(event_id)
    assert stored is not None
    assert stored.processed_at is not None
    assert stored.attempt_count == 0


async def test_redelivered_event_is_processed_exactly_once(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_count = 0
    original = BillingService.handle_event

    async def _counting_handle_event(self: BillingService, event: WebhookEvent) -> None:
        nonlocal call_count
        call_count += 1
        await original(self, event)

    monkeypatch.setattr(BillingService, "handle_event", _counting_handle_event)
    event_id = f"evt_{uuid4().hex}"
    body = _stripe_event(event_id=event_id)

    first = await _post_webhook(client, body)
    second = await _post_webhook(client, body)

    assert first.status_code == 200
    assert second.status_code == 200
    assert call_count == 1

    repo = WebhookEventRepository(session)
    stored = await repo.get_by_id(event_id)
    assert stored is not None
    assert stored.attempt_count == 0


# --- handler failure ------------------------------------------------------------------


async def test_handler_exception_records_error_and_returns_500(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(self: BillingService, event: WebhookEvent) -> None:  # noqa: ARG001 -- matches handle_event's signature
        raise RuntimeError("boom")

    monkeypatch.setattr(BillingService, "handle_event", _boom)
    event_id = f"evt_{uuid4().hex}"

    response = await _post_webhook(client, _stripe_event(event_id=event_id))

    assert response.status_code == 500
    repo = WebhookEventRepository(session)
    stored = await repo.get_by_id(event_id)
    assert stored is not None
    assert stored.processed_at is None
    assert stored.error == "boom"
    assert stored.attempt_count == 1


# --- metrics (T-202) -----------------------------------------------------------------


async def test_webhook_call_observes_stripe_webhook_duration_histogram(
    client: httpx.AsyncClient,
) -> None:
    before = REGISTRY.get_sample_value("forge_stripe_webhook_seconds_count") or 0.0

    response = await _post_webhook(client, _stripe_event(event_id=f"evt_{uuid4().hex}"))

    assert response.status_code == 200
    after = REGISTRY.get_sample_value("forge_stripe_webhook_seconds_count")
    assert after == before + 1


async def test_retry_after_failure_succeeds_and_marks_processed(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(self: BillingService, event: WebhookEvent) -> None:  # noqa: ARG001 -- matches handle_event's signature
        raise RuntimeError("boom")

    event_id = f"evt_{uuid4().hex}"
    body = _stripe_event(event_id=event_id)

    monkeypatch.setattr(BillingService, "handle_event", _boom)
    failed_response = await _post_webhook(client, body)
    assert failed_response.status_code == 500

    monkeypatch.undo()
    retried_response = await _post_webhook(client, body)

    assert retried_response.status_code == 200
    repo = WebhookEventRepository(session)
    stored = await repo.get_by_id(event_id)
    assert stored is not None
    assert stored.processed_at is not None
    assert stored.attempt_count == 1
