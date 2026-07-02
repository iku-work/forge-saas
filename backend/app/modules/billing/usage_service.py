"""
UsageService (T-132): the write/read API a metered feature calls to record
and aggregate usage. Bound to a single org at construction -- same "org_id is
required, no default" rule as app.core.repository.BaseRepository (§0.2) --
since a service that recorded usage against an org it wasn't given would be
exactly the cross-tenant bug that rule exists to make structurally
impossible.

record()'s idempotency_key dedup returns the *existing* row rather than
letting a second insert hit UsageRecord.idempotency_key's unique constraint
and raise -- a retried caller (e.g. a flaky client re-POSTing the same
metered action) wants the same result back, not an IntegrityError to handle.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.billing.repository import UsageRecordRepository
from app.modules.billing.usage_models import UsageRecord


class UsageService:
    def __init__(self, session: AsyncSession, org_id: UUID) -> None:
        self._org_id = org_id
        self._records = UsageRecordRepository(session)

    async def record(
        self, *, metric: str, quantity: int, idempotency_key: str | None = None
    ) -> UsageRecord:
        if idempotency_key is not None:
            existing = await self._records.get_by_idempotency_key(idempotency_key)
            if existing is not None:
                return existing

        return await self._records.create(
            org_id=self._org_id,
            metric=metric,
            quantity=quantity,
            idempotency_key=idempotency_key,
        )

    async def aggregate(self, *, metric: str, since: datetime, until: datetime) -> int:
        return await self._records.sum_quantity(
            org_id=self._org_id, metric=metric, since=since, until=until
        )
