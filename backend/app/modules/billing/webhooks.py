"""
Stripe webhook endpoint (T-030): the only billing route with no auth
dependency -- Stripe's own HMAC signature in the Stripe-Signature header is
the authentication, verified against STRIPE_WEBHOOK_SECRET. Idempotency is
enforced by persisting the event under Stripe's own id *before* dispatching:
a redelivery for an id whose processed_at is already set short-circuits to
200 without re-running the handler, and one whose handler previously raised
is retried from scratch (its WebhookEvent row exists but processed_at is
still null).

No rate limiting here, unlike every other router in this codebase --
Stripe's own retry behavior on a 5xx is the throttle that matters, and a
sliding-window limit could drop a legitimate burst of billing events for an
active org.
"""

import logging
from typing import Annotated, cast

import stripe
from fastapi import APIRouter, Depends, Request, Response
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.dependencies import get_redis
from app.core.exceptions import DomainError
from app.core.metrics import FORGE_STRIPE_WEBHOOK_SECONDS
from app.modules.billing.repository import WebhookEventRepository
from app.modules.billing.service import BillingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


@router.post("/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> Response:
    # impersonation-ok: a server-to-server callback from Stripe, authenticated
    # by the signature check below and never by a user bearer token -- there is
    # no impersonation session for this route to be inside of.
    settings = get_settings()
    raw_body = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        # stripe-python ships py.typed but construct_event itself has no
        # parameter/return annotations -- mypy strict's no-untyped-call has
        # no other way to know this returns a stripe.Event. (mypy 2.3 does
        # see annotations here and reports the ignore as unused; the pin in
        # pyproject.toml is what keeps one answer. Whoever lifts that pin
        # drops this comment and the ignore -- the cast below is already
        # what the newer checker asks for.)
        #
        # The secret is `str | None` on Settings, but validate_billing_settings()
        # refuses to boot with billing enabled and it unset, and this route only
        # exists when billing is enabled -- so it is never None here. A cast,
        # not a runtime guard: the branch would be unreachable, and billing is
        # held to 100% coverage.
        stripe_event = stripe.Webhook.construct_event(  # type: ignore[no-untyped-call]
            raw_body, sig_header, cast("str", settings.STRIPE_WEBHOOK_SECRET)
        )
    except (stripe.SignatureVerificationError, ValueError) as exc:
        logger.warning("billing.webhook_signature_invalid")
        raise DomainError(
            "errors.invalid_webhook_signature", fallback="Invalid webhook signature"
        ) from exc

    with FORGE_STRIPE_WEBHOOK_SECONDS.time():
        repo = WebhookEventRepository(session)
        existing = await repo.get_by_id(stripe_event["id"])
        if existing is not None and existing.processed_at is not None:
            return Response(status_code=200)

        if existing is None:
            existing = await repo.create(
                event_id=stripe_event["id"],
                event_type=stripe_event["type"],
                payload=stripe_event.to_dict(),
            )
            await session.commit()

        try:
            # Broad except is intentional: any failure here must be recorded so
            # Stripe's retry re-runs handle_event, then re-raised so the global
            # handler logs it and responds 500.
            await BillingService(session, redis).handle_event(existing)
        except Exception as exc:
            await repo.mark_failed(existing, error=str(exc))
            await session.commit()
            raise

        await repo.mark_processed(existing)
        await session.commit()
        return Response(status_code=200)
