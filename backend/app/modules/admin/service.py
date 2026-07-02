"""
AdminFeatureFlagService (T-122): superadmin support/beta-testing overrides on
top of T-120's FeatureFlagService -- lets a superadmin flip a flag for one org
without touching that org's plan. Every mutation writes an AuditEvent
(§0.1: cross-module audit writes go through AuditService, never
AuditRepository directly) and reuses FeatureFlagService.set_override /
delete_override, which already invalidate the flag cache themselves (T-120).
"""

from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.admin.schemas import AdminFeatureFlagState
from app.modules.audit.service import AuditService
from app.modules.feature_flags.repository import OrgFeatureFlagRepository
from app.modules.feature_flags.service import FeatureFlagService
from app.modules.tenancy.models import Organization


class AdminFeatureFlagService:
    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self._session = session
        self._flags = FeatureFlagService(session, redis)
        self._audit = AuditService(session)

    async def resolved_flags(self, org: Organization) -> dict[str, AdminFeatureFlagState]:
        resolved = await self._flags.all_enabled(org)
        overrides = await OrgFeatureFlagRepository(self._session, org.id).get_overrides()
        names = set(resolved) | set(overrides)
        return {
            name: AdminFeatureFlagState(
                resolved=resolved.get(name, False), override=overrides.get(name)
            )
            for name in names
        }

    async def set_override(  # noqa: PLR0913 -- mirrors AuditService.log's own field set
        self,
        *,
        org: Organization,
        flag: str,
        enabled: bool,
        actor_id: UUID,
        ip: str | None,
        ua: str | None,
    ) -> None:
        await self._flags.set_override(org.id, flag, enabled)
        await self._audit.log(
            action="admin.feature_flag.set",
            resource_type="feature_flag",
            resource_id=flag,
            org_id=org.id,
            actor_id=actor_id,
            diff={"flag": flag, "enabled": enabled},
            ip=ip,
            ua=ua,
        )

    async def delete_override(
        self,
        *,
        org: Organization,
        flag: str,
        actor_id: UUID,
        ip: str | None,
        ua: str | None,
    ) -> None:
        await self._flags.delete_override(org.id, flag)
        await self._audit.log(
            action="admin.feature_flag.set",
            resource_type="feature_flag",
            resource_id=flag,
            org_id=org.id,
            actor_id=actor_id,
            diff={"flag": flag, "enabled": None},
            ip=ip,
            ua=ua,
        )
