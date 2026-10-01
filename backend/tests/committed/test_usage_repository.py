"""
Verifies T-132's UsageRecordRepository: idempotency_key lookups, sum_quantity's
time-window aggregation, and the unreported-record sweep report_usage_to_stripe
(app.workers.jobs.usage) relies on. Same real-Postgres testcontainers pattern
as test_billing_repository.py.

Organization.__table__ is included (unlike test_billing_repository.py's
Plan/Subscription/WebhookEvent) because UsageRecord uses OrgScopedMixin,
which declares a real `foreign_key="organization.id"` -- every org_id used
below has to be a row that actually exists, not just a bare uuid4(). Plan is
along for the ride: Organization.plan_id FKs to it, so Postgres won't create
the `organization` table without it, even though these tests never touch a
Plan row directly.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from app.modules.billing.repository import UsageRecordRepository
from app.modules.tenancy.models import Organization
from app.modules.tenancy.repository import OrganizationRepository


@pytest.fixture
def usage(session: AsyncSession) -> UsageRecordRepository:
    return UsageRecordRepository(session)


async def _make_org(session: AsyncSession) -> Organization:
    return await OrganizationRepository(session).create(name="Acme", slug=f"acme-{uuid4().hex[:8]}")


async def test_create_then_get_by_idempotency_key_round_trips(
    usage: UsageRecordRepository, session: AsyncSession
) -> None:
    org = await _make_org(session)
    created = await usage.create(
        org_id=org.id, metric="api_calls", quantity=3, idempotency_key="key-1"
    )

    found = await usage.get_by_idempotency_key("key-1")

    assert found is not None
    assert found.id == created.id
    assert found.org_id == org.id
    assert found.quantity == 3


async def test_get_by_idempotency_key_unknown_returns_none(
    usage: UsageRecordRepository,
) -> None:
    assert await usage.get_by_idempotency_key("does-not-exist") is None


async def test_create_defaults_occurred_at_to_now(
    usage: UsageRecordRepository, session: AsyncSession
) -> None:
    org = await _make_org(session)
    before = datetime.now(UTC)

    record = await usage.create(org_id=org.id, metric="exports", quantity=1)

    assert record.occurred_at >= before


async def test_sum_quantity_respects_time_window(
    usage: UsageRecordRepository, session: AsyncSession
) -> None:
    org = await _make_org(session)
    other_org = await _make_org(session)
    await usage.create(
        org_id=org.id,
        metric="api_calls",
        quantity=5,
        occurred_at=datetime(2026, 1, 10, tzinfo=UTC),
    )
    await usage.create(
        org_id=org.id,
        metric="api_calls",
        quantity=7,
        occurred_at=datetime(2026, 1, 20, tzinfo=UTC),
    )
    # Outside the queried window -- must not be counted.
    await usage.create(
        org_id=org.id,
        metric="api_calls",
        quantity=100,
        occurred_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    # Different metric -- must not be counted.
    await usage.create(
        org_id=org.id,
        metric="ai_tokens",
        quantity=100,
        occurred_at=datetime(2026, 1, 15, tzinfo=UTC),
    )
    # Different org -- must not be counted.
    await usage.create(
        org_id=other_org.id,
        metric="api_calls",
        quantity=100,
        occurred_at=datetime(2026, 1, 15, tzinfo=UTC),
    )

    total = await usage.sum_quantity(
        org_id=org.id,
        metric="api_calls",
        since=datetime(2026, 1, 1, tzinfo=UTC),
        until=datetime(2026, 2, 1, tzinfo=UTC),
    )

    assert total == 12


async def test_sum_quantity_with_no_matching_records_returns_zero(
    usage: UsageRecordRepository,
) -> None:
    total = await usage.sum_quantity(
        org_id=uuid4(),
        metric="api_calls",
        since=datetime(2026, 1, 1, tzinfo=UTC),
        until=datetime(2026, 2, 1, tzinfo=UTC),
    )

    assert total == 0


async def test_list_unreported_excludes_reported_records(
    usage: UsageRecordRepository, session: AsyncSession
) -> None:
    org = await _make_org(session)
    unreported = await usage.create(org_id=org.id, metric="api_calls", quantity=1)
    reported = await usage.create(org_id=org.id, metric="api_calls", quantity=2)
    await usage.mark_reported([reported], stripe_usage_record_id="evt_1")

    found = await usage.list_unreported()

    found_ids = {record.id for record in found}
    assert unreported.id in found_ids
    assert reported.id not in found_ids


async def test_mark_reported_sets_timestamp_and_stripe_id(
    usage: UsageRecordRepository, session: AsyncSession
) -> None:
    org = await _make_org(session)
    record = await usage.create(org_id=org.id, metric="api_calls", quantity=1)
    assert record.reported_to_stripe_at is None

    await usage.mark_reported([record], stripe_usage_record_id="evt_42")

    assert record.reported_to_stripe_at is not None
    assert record.stripe_usage_record_id == "evt_42"
