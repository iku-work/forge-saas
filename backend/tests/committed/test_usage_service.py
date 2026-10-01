"""
Verifies T-132's UsageService: record() is bound to the org it was
constructed with (never takes org_id per call), a repeated idempotency_key
returns the original row instead of inserting a duplicate (UsageRecord.
idempotency_key's unique constraint would otherwise raise on the second
insert), and aggregate() delegates straight to the repository's time-window
sum. Same real-Postgres testcontainers pattern as test_billing_service.py.

Organization.__table__ is included because UsageRecord uses OrgScopedMixin,
which declares a real `foreign_key="organization.id"` -- see
test_usage_repository.py's module docstring for the same note (Plan is
along for the ride there too, since Organization.plan_id FKs to it).

100% coverage required on app.modules.billing.usage_service (agents.md T-132).
"""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from app.modules.billing.usage_models import UsageRecord
from app.modules.billing.usage_service import UsageService
from app.modules.tenancy.models import Organization
from app.modules.tenancy.repository import OrganizationRepository


async def _make_org(session: AsyncSession) -> Organization:
    return await OrganizationRepository(session).create(name="Acme", slug=f"acme-{uuid4().hex[:8]}")


async def test_record_creates_a_usage_record(session: AsyncSession) -> None:
    org = await _make_org(session)
    service = UsageService(session, org.id)

    record = await service.record(metric="api_calls", quantity=5)

    assert record.org_id == org.id
    assert record.metric == "api_calls"
    assert record.quantity == 5


async def test_record_without_idempotency_key_allows_repeats(session: AsyncSession) -> None:
    org = await _make_org(session)
    service = UsageService(session, org.id)

    first = await service.record(metric="api_calls", quantity=1)
    second = await service.record(metric="api_calls", quantity=1)

    assert first.id != second.id


async def test_record_with_repeated_idempotency_key_returns_existing_record(
    session: AsyncSession,
) -> None:
    org = await _make_org(session)
    service = UsageService(session, org.id)

    first = await service.record(metric="api_calls", quantity=3, idempotency_key="req-1")
    second = await service.record(metric="api_calls", quantity=3, idempotency_key="req-1")

    assert first.id == second.id
    result = await session.execute(
        select(UsageRecord).where(UsageRecord.idempotency_key == "req-1")
    )
    assert len(result.scalars().all()) == 1


async def test_aggregate_sums_within_window_and_org(session: AsyncSession) -> None:
    org = await _make_org(session)
    other_org = await _make_org(session)
    service = UsageService(session, org.id)
    other_service = UsageService(session, other_org.id)

    await service.record(metric="api_calls", quantity=4)
    await service.record(metric="api_calls", quantity=6)
    await other_service.record(metric="api_calls", quantity=1000)

    total = await service.aggregate(
        metric="api_calls",
        since=datetime(2020, 1, 1, tzinfo=UTC),
        until=datetime(2099, 1, 1, tzinfo=UTC),
    )

    assert total == 10
