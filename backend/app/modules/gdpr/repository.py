"""
DB access for ConsentRecord and DataExportJob (T-032/T-033). ConsentRepository
is deliberately not a BaseRepository subclass (ConsentRecord is user-scoped,
not org-scoped — see models.py) and deliberately has no update()/delete():
§0.3 immutability means there must be no code path that can mutate or remove
a consent row once written. DataExportJobRepository is mutable (a job's
status/download_url change as the export job (T-037) progresses) but is
likewise user-scoped rather than a BaseRepository subclass.
"""

from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models._mixins import utcnow
from app.modules.gdpr.models import ConsentRecord, DataExportJob


class ConsentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, **kwargs: Any) -> ConsentRecord:  # noqa: ANN401 — forwarded to ConsentRecord(**kwargs)
        record = ConsentRecord(**kwargs)
        self.session.add(record)
        await self.session.flush()
        return record

    async def list_for_user(self, user_id: UUID) -> list[ConsentRecord]:
        m = cast("Any", ConsentRecord)
        stmt = select(ConsentRecord).where(m.user_id == user_id).order_by(m.ts.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class DataExportJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, *, user_id: UUID) -> DataExportJob:
        job = DataExportJob(user_id=user_id)
        self.session.add(job)
        await self.session.flush()
        return job

    async def get_by_id(self, job_id: UUID) -> DataExportJob | None:
        m = cast("Any", DataExportJob)
        stmt = select(DataExportJob).where(m.id == job_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def expire_ready_past_expiry(self) -> int:
        # Called by app.workers.scheduler.expire_data_export_urls (T-034) —
        # a download link past its expiry is no longer valid, so the row is
        # marked "expired" rather than deleted (the user's export history
        # stays visible in GET /me/export/{job_id}).
        m = cast("Any", DataExportJob)
        stmt = select(DataExportJob).where(
            m.status == "ready", m.expires_at.is_not(None), m.expires_at < utcnow()
        )
        result = await self.session.execute(stmt)
        jobs = result.scalars().all()
        for job in jobs:
            job.status = "expired"
            job.download_url = None
        await self.session.flush()
        return len(jobs)
