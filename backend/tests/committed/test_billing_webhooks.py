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
from prometheus_client import REGISTRY
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from app.core.config import get_settings
from app.modules.billing.models import WebhookEvent
from app.modules.billing.repository import WebhookEventRepository
from app.modules.billing.service import BillingService
from tests._client import serve_app

# T-291: this file needs billing enabled -- its routes, its tables, or
# both. CI's module-matrix job deselects it in the combination that turns
# every optional module off.
pytestmark = pytest.mark.module_dependent


@pytest.fixture
async def client(
    get_session_override: Callable[[], AsyncGenerator[AsyncSession, None]],
    redis_client: Redis,
    product_settings_session: None,  # noqa: ARG001 -- the middleware needs it
) -> AsyncGenerator[httpx.AsyncClient, None]:
    # raise_app_exceptions=False: ServerErrorMiddleware sends the registered
    # Exception handler's 500 response *and then* re-raises for visibility in
    # dev -- test_handler_exception_records_error_and_returns_500 below
    # exercises that path and wants the Response, not the re-raised exception.
    async with serve_app(
        get_session_override=get_session_override,
        redis=redis_client,
        raise_app_exceptions=False,
    ) as c:
        yield c


def _stripe_event(
    *, event_id: str, event_type: str = "checkout.session.completed"
) -> dict[str, Any]:
    # A type with no handler in BillingService.handle_event's dispatch --
    # these tests exercise the webhook/idempotency plumbing, not any
    # particular business outcome, so a no-op type keeps setup minimal.
    return {
        "id": event_id,
        # `object: "event"` matters: construct_event unwraps EventBridge and
        # EventGrid envelopes, and tells a bare event from one by this field.
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


async def test_a_decimal_amount_in_the_payload_is_stored(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """The bug that stopped every real delivery in production.

    Stripe sends `*_decimal` fields as JSON *strings* -- "4900" -- and the SDK
    coerces them to `Decimal` on the way in. The handler stored
    `stripe_event.to_dict()` into a JSONB column, json.dumps refuses a
    Decimal, and the INSERT raised before the event was ever recorded. Every
    delivery answered 500 while *recording* the event, which is the worst
    place to fail: no row was written, so handle_event never ran, a paid
    subscription never reached the database, and there was no event row on
    which to see a failure. Stripe retried into the same wall.

    The shape below is copied from a real `customer.subscription.created` off
    the live account, and the `object` discriminators are load-bearing.
    Coercion is not keyed on the field name: the SDK picks a typed class from
    `object` -- subscription, plan, price -- and only those classes convert
    their `*_decimal` strings. Drop the discriminators and the same payload
    deserialises into a plain StripeObject with no Decimal anywhere, which is
    how a first attempt at this test passed against the very bug it was
    written for.
    """
    event_id = f"evt_{uuid4().hex}"
    body = _stripe_event(event_id=event_id)
    body["data"]["object"] = {
        "id": "sub_1",
        "object": "subscription",
        "plan": {"object": "plan", "amount_decimal": "4900"},
        "items": {
            "object": "list",
            "data": [
                {
                    "object": "subscription_item",
                    "price": {"object": "price", "unit_amount_decimal": "4900"},
                }
            ],
        },
    }

    response = await _post_webhook(client, body)

    assert response.status_code == 200
    stored = await WebhookEventRepository(session).get_by_id(event_id)
    assert stored is not None
    # Rendered back as the string Stripe sent, not dropped and not a float.
    assert stored.payload["data"]["object"]["plan"]["amount_decimal"] == "4900"


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
