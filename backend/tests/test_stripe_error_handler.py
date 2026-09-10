"""What the caller sees when Stripe refuses.

Before this handler every StripeError fell through to the catch-all: a 500,
"An unexpected error occurred", and a Sentry event filed as an application
bug. The likeliest cause is not a bug -- it is a Plan row whose
stripe_price_id_* is not an ID at all. app.core.seed ships
`price_pro_monthly` as a placeholder, and a production database seeded from
it answers POST /billing/checkout-session with a 500 that says nothing about
prices. That happened, and took a Stripe API probe to identify.

Driven through a throwaway app rather than the real one: what is pinned is
the mapping from a raised StripeError to a response, and the real app would
drag in auth, an org and a Stripe customer to reach the same three lines.
"""

import logging

import httpx
import pytest
import stripe
from fastapi import FastAPI

from app.core.handlers import register_exception_handlers


def _app(exc: Exception) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise exc

    return app


async def _get(app: FastAPI) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/boom")


async def test_a_missing_price_is_a_502_not_a_500() -> None:
    """The exact failure that shipped: seed's placeholder price ID reaching
    Stripe. 5xx either way, but 502 says "upstream refused" rather than
    "this service is broken", and the code is something a client can match."""
    response = await _get(
        _app(stripe.InvalidRequestError("No such price: 'price_pro_monthly'", param="line_items"))
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "payment_provider_error"


async def test_the_stripe_message_never_reaches_the_caller() -> None:
    """ "No such price: 'price_pro_monthly'" tells an attacker about the
    billing configuration and tells a customer nothing they can act on. It
    belongs in the log, which is asserted separately below."""
    response = await _get(
        _app(stripe.InvalidRequestError("No such price: 'price_pro_monthly'", param="line_items"))
    )

    assert "price_pro_monthly" not in response.text


async def test_the_operator_gets_the_detail_in_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR, logger="app.core.handlers"):
        await _get(
            _app(
                stripe.InvalidRequestError("No such price: 'price_pro_monthly'", param="line_items")
            )
        )

    assert "price_pro_monthly" in caplog.text


async def test_a_declined_card_is_402_and_says_so() -> None:
    """The one StripeError the caller can act on. Stripe writes
    `user_message` for this purpose, so it is passed through."""
    exc = stripe.CardError("Your card was declined.", param="number", code="card_declined")

    response = await _get(_app(exc))

    assert response.status_code == 402
    assert response.json()["error"]["code"] == "card_declined"
