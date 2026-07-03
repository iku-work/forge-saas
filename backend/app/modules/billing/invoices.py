"""
InvoiceService (T-133): shows an org's Stripe invoice history without
storing invoices ourselves -- Stripe stays the source of truth. A 5-minute
Redis cache absorbs repeat page loads of the same cursor; BillingService's
webhook dispatch (see its `invoice.*` cases) calls `invalidate` right after
a payment succeeds/fails so a refreshed page never serves a stale status.

Cursor here is Stripe's own `starting_after` invoice id, not this repo's
internal (created_at, id) cursor convention (§0.3) -- there's no local table
to order by; Stripe's List API is itself cursored on object id.

Constructor takes only `session`/`redis`, mirroring FeatureFlagService
(app.modules.feature_flags.service) rather than agents.md's literal
zero-arg instance methods: both `list_for_org` and `invalidate` need an org
identity, and the webhook handler that calls `invalidate` only ever has an
`org_id` (off the Subscription row), never a loaded Organization -- so
`invalidate` takes a bare UUID and `list_for_org` takes the Organization
the router already resolved via `get_current_org`.
"""

import json
from datetime import UTC, datetime
from uuid import UUID

import stripe
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.schemas import AppBaseModel
from app.modules.tenancy.models import Organization

_CACHE_PREFIX = "invoices"
_CACHE_TTL_SECONDS = 300


def _epoch_to_datetime(value: int) -> datetime:
    return datetime.fromtimestamp(value, tz=UTC)


def _cache_key(org_id: UUID, cursor: str | None) -> str:
    return f"{_CACHE_PREFIX}:{org_id}:{cursor or 'first'}"


class InvoiceDTO(AppBaseModel):
    id: str
    number: str | None
    amount_paid: int
    currency: str
    status: str | None
    hosted_invoice_url: str | None
    invoice_pdf: str | None
    period_start: datetime
    period_end: datetime
    created_at: datetime


def _to_dto(invoice: stripe.Invoice) -> InvoiceDTO:
    return InvoiceDTO(
        id=invoice.id,
        number=invoice.number,
        amount_paid=invoice.amount_paid,
        currency=invoice.currency,
        status=invoice.status,
        hosted_invoice_url=invoice.hosted_invoice_url,
        invoice_pdf=invoice.invoice_pdf,
        period_start=_epoch_to_datetime(invoice.period_start),
        period_end=_epoch_to_datetime(invoice.period_end),
        created_at=_epoch_to_datetime(invoice.created),
    )


class InvoiceService:
    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self._session = session
        self._redis = redis
        stripe.api_key = get_settings().STRIPE_SECRET_KEY

    async def list_for_org(
        self, org: Organization, *, after_cursor: str | None = None, limit: int = 20
    ) -> tuple[list[InvoiceDTO], str | None]:
        cache_key = _cache_key(org.id, after_cursor)
        cached = await self._redis.get(cache_key)
        if cached is not None:
            payload = json.loads(cached)
            items = [InvoiceDTO.model_validate(item) for item in payload["items"]]
            return items, payload["next_cursor"]

        if org.stripe_customer_id is None:
            return [], None

        stripe_invoices = (
            stripe.Invoice.list(
                customer=org.stripe_customer_id, limit=limit, starting_after=after_cursor
            )
            if after_cursor is not None
            else stripe.Invoice.list(customer=org.stripe_customer_id, limit=limit)
        )

        items = [_to_dto(invoice) for invoice in stripe_invoices.data]
        next_cursor = items[-1].id if stripe_invoices.has_more and items else None

        await self._redis.set(
            cache_key,
            json.dumps(
                {
                    "items": [item.model_dump(mode="json") for item in items],
                    "next_cursor": next_cursor,
                }
            ),
            ex=_CACHE_TTL_SECONDS,
        )
        return items, next_cursor

    async def invalidate(self, org_id: UUID) -> None:
        keys = [key async for key in self._redis.scan_iter(match=f"{_CACHE_PREFIX}:{org_id}:*")]
        if keys:
            await self._redis.delete(*keys)
