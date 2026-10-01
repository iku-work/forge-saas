"""
Verifies T-133's InvoiceService: an org with no `stripe_customer_id` gets an
empty list without ever calling Stripe (it can't have any invoices), a
Stripe response maps field-for-field onto InvoiceDTO, a result is cached for
5 minutes so a repeat call with the same cursor never calls Stripe again,
`has_more` on the Stripe response turns into a `next_cursor` equal to the
last returned invoice's id (Stripe's own `starting_after` cursor -- see the
module docstring), and `invalidate` only clears the calling org's cached
pages, never another org's.

stripe.Invoice.list_async is monkeypatched -- this file verifies
InvoiceService's own request/cache/DTO logic, not Stripe's contract. T-206
switched this call site onto stripe's `_async` method variant (wrapped in
app.core.resilience's async `@resilient("stripe")`), so the monkeypatched
replacements below are themselves `async def`. Same real-Postgres +
real-Redis testcontainers pattern as test_billing_checkout.py.
"""

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
import stripe
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from app.modules.billing.invoices import InvoiceService
from app.modules.tenancy.models import Organization


@pytest.fixture
def service(session: AsyncSession, redis: Redis) -> InvoiceService:
    return InvoiceService(session, redis)


async def _make_org(
    session: AsyncSession, *, stripe_customer_id: str | None = None
) -> Organization:
    org = Organization(
        name="Acme", slug=f"acme-{uuid4().hex[:8]}", stripe_customer_id=stripe_customer_id
    )
    session.add(org)
    await session.flush()
    return org


class _FakeInvoiceList:
    def __init__(self, data: list[SimpleNamespace], *, has_more: bool) -> None:
        self.data = data
        self.has_more = has_more


def _invoice(invoice_id: str) -> SimpleNamespace:
    # SimpleNamespace, not a dict: InvoiceService reads Invoice objects via
    # attribute access (stripe.Invoice's typed fields), not dict subscripting.
    return SimpleNamespace(
        id=invoice_id,
        number="INV-001",
        amount_paid=2900,
        currency="usd",
        status="paid",
        hosted_invoice_url=f"https://invoice.stripe.com/{invoice_id}",
        invoice_pdf=f"https://invoice.stripe.com/{invoice_id}.pdf",
        period_start=1735689600,
        period_end=1738368000,
        created=1735689600,
    )


# --- no Stripe customer -----------------------------------------------------------------


async def test_list_for_org_returns_empty_without_calling_stripe_when_no_customer(
    service: InvoiceService, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = await _make_org(session)

    async def _list(**_kwargs: object) -> _FakeInvoiceList:
        raise AssertionError("stripe.Invoice.list_async should not be called")

    monkeypatch.setattr(stripe.Invoice, "list_async", _list)

    items, next_cursor = await service.list_for_org(org)

    assert items == []
    assert next_cursor is None


# --- DTO mapping + cursor ----------------------------------------------------------------


async def test_list_for_org_maps_stripe_fields_onto_dto(
    service: InvoiceService, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = await _make_org(session, stripe_customer_id="cus_1")

    async def _list(**_kwargs: object) -> _FakeInvoiceList:
        return _FakeInvoiceList([_invoice("in_1")], has_more=False)

    monkeypatch.setattr(stripe.Invoice, "list_async", _list)

    items, next_cursor = await service.list_for_org(org)

    assert len(items) == 1
    dto = items[0]
    assert dto.id == "in_1"
    assert dto.number == "INV-001"
    assert dto.amount_paid == 2900
    assert dto.currency == "usd"
    assert dto.status == "paid"
    assert dto.hosted_invoice_url == "https://invoice.stripe.com/in_1"
    assert dto.invoice_pdf == "https://invoice.stripe.com/in_1.pdf"
    assert next_cursor is None


async def test_list_for_org_next_cursor_is_last_invoice_id_when_has_more(
    service: InvoiceService, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = await _make_org(session, stripe_customer_id="cus_1")

    async def _list(**_kwargs: object) -> _FakeInvoiceList:
        return _FakeInvoiceList([_invoice("in_1"), _invoice("in_2")], has_more=True)

    monkeypatch.setattr(stripe.Invoice, "list_async", _list)

    _items, next_cursor = await service.list_for_org(org)

    assert next_cursor == "in_2"


async def test_list_for_org_passes_customer_and_cursor_to_stripe(
    service: InvoiceService, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = await _make_org(session, stripe_customer_id="cus_1")
    captured: dict[str, Any] = {}

    async def _list(**kwargs: object) -> _FakeInvoiceList:
        captured.update(kwargs)
        return _FakeInvoiceList([], has_more=False)

    monkeypatch.setattr(stripe.Invoice, "list_async", _list)

    await service.list_for_org(org, after_cursor="in_1", limit=10)

    assert captured["customer"] == "cus_1"
    assert captured["starting_after"] == "in_1"
    assert captured["limit"] == 10


# --- caching -------------------------------------------------------------------------------


async def test_list_for_org_second_call_uses_cache_not_stripe(
    service: InvoiceService, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = await _make_org(session, stripe_customer_id="cus_1")
    call_count = 0

    async def _list(**_kwargs: object) -> _FakeInvoiceList:
        nonlocal call_count
        call_count += 1
        return _FakeInvoiceList([_invoice("in_1")], has_more=False)

    monkeypatch.setattr(stripe.Invoice, "list_async", _list)

    first_items, _ = await service.list_for_org(org)
    second_items, _ = await service.list_for_org(org)

    assert call_count == 1
    assert [item.id for item in first_items] == [item.id for item in second_items]


async def test_list_for_org_different_cursor_is_a_separate_cache_entry(
    service: InvoiceService, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = await _make_org(session, stripe_customer_id="cus_1")
    call_count = 0

    async def _list(**_kwargs: object) -> _FakeInvoiceList:
        nonlocal call_count
        call_count += 1
        return _FakeInvoiceList([_invoice(f"in_{call_count}")], has_more=False)

    monkeypatch.setattr(stripe.Invoice, "list_async", _list)

    await service.list_for_org(org, after_cursor=None)
    await service.list_for_org(org, after_cursor="in_1")

    assert call_count == 2


# --- invalidate -----------------------------------------------------------------------------


async def test_invalidate_clears_only_the_target_orgs_cache(
    service: InvoiceService, session: AsyncSession, redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    org_a = await _make_org(session, stripe_customer_id="cus_a")
    org_b = await _make_org(session, stripe_customer_id="cus_b")

    async def _list(**_kwargs: object) -> _FakeInvoiceList:
        return _FakeInvoiceList([_invoice("in_1")], has_more=False)

    monkeypatch.setattr(stripe.Invoice, "list_async", _list)

    await service.list_for_org(org_a)
    await service.list_for_org(org_b)

    await service.invalidate(org_a.id)

    assert await redis.get(f"invoices:{org_a.id}:first") is None
    assert await redis.get(f"invoices:{org_b.id}:first") is not None
