"""
Business logic for the GDPR module (T-033): data export requests, the
right-to-be-forgotten delete-me flow, and the append-only consent log.

request_export and delete_me each end by enqueueing their ARQ counterpart --
export_user_data (T-037) and purge_user_data (T-036) respectively, both
registered on the worker in app.workers.main. delete_me's grace period is
read from settings.GDPR_PURGE_GRACE_DAYS so an operator can shorten or
lengthen it per environment without a code change; purge_user_data itself
re-checks deleted_at before doing anything, so a delete reversed during the
grace period is a no-op rather than a race.
"""

from datetime import timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import jobs
from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.models._mixins import utcnow
from app.modules.audit.service import AuditService
from app.modules.auth.models import User
from app.modules.auth.repository import UserRepository
from app.modules.gdpr.models import ConsentRecord, DataExportJob
from app.modules.gdpr.repository import ConsentRepository, DataExportJobRepository


class GDPRService:
    def __init__(self, session: AsyncSession) -> None:
        self._jobs = DataExportJobRepository(session)
        self._consents = ConsentRepository(session)
        self._users = UserRepository(session)
        self._audit = AuditService(session)

    async def request_export(self, user_id: UUID) -> DataExportJob:
        job = await self._jobs.create(user_id=user_id)
        await self._enqueue_export_job(user_id, job.id)
        return job

    async def get_export_job(self, user_id: UUID, job_id: UUID) -> DataExportJob:
        job = await self._jobs.get_by_id(job_id)
        if job is None or job.user_id != user_id:
            # Same shape whether the job doesn't exist or belongs to someone
            # else -- see app.modules.auth.service.revoke_session for why.
            raise NotFoundError("errors.export_job_not_found", fallback="Export job not found")
        return job

    async def delete_me(self, user: User) -> None:
        await self._users.update(user.id, deleted_at=utcnow())
        await self._audit.log(
            action="user.deletion_requested",
            resource_type="user",
            resource_id=str(user.id),
            org_id=None,
            actor_id=user.id,
        )
        await self._enqueue_purge_job(user.id)

    async def record_consent(  # noqa: PLR0913 -- matches the T-032 ConsentRecord field set
        self, *, user_id: UUID, consent_type: str, granted: bool, version: str, ip: str, ua: str
    ) -> ConsentRecord:
        return await self._consents.create(
            user_id=user_id,
            consent_type=consent_type,
            granted=granted,
            version=version,
            ip_address=ip,
            user_agent=ua,
        )

    async def list_consents(self, user_id: UUID) -> list[ConsentRecord]:
        return await self._consents.list_for_user(user_id)

    async def _enqueue_export_job(self, user_id: UUID, job_id: UUID) -> None:
        await jobs.enqueue_job("export_user_data", user_id=user_id, job_id=job_id)

    async def _enqueue_purge_job(self, user_id: UUID) -> None:
        grace_days = get_settings().GDPR_PURGE_GRACE_DAYS
        await jobs.enqueue_job(
            "purge_user_data", user_id=user_id, _defer_by=timedelta(days=grace_days)
        )
