"""
DB access for the feature_flags module's org-level overrides (T-120):
OrgFeatureFlag is the second of the "two-layer flags" architecture (the
first layer, Plan.features, is read through
app.modules.billing.dependencies.resolve_org_subscription instead -- see
app.modules.feature_flags.service for how the two are combined).

OrgFeatureFlag's table was created by T-025's tenancy migration (it ships in
the same migration as Organization/Membership/Invitation), and its model
still lives in app.modules.tenancy.models -- moving it would need a new
migration for no schema change, so this repository just reads/writes that
existing table rather than duplicating the model here.

Bespoke class, not a BaseRepository subclass: OrgFeatureFlag has no
`deleted_at` (an override is deleted for real when revoked, never
soft-deleted), so it doesn't satisfy BaseRepository's `_OrgScopedRecord`
shape -- same reasoning as app.modules.tenancy.repository's classes.
"""

from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.tenancy.models import OrgFeatureFlag


class OrgFeatureFlagRepository:
    def __init__(self, session: AsyncSession, org_id: UUID) -> None:
        self.session = session
        self.org_id = org_id

    async def get_overrides(self) -> dict[str, bool]:
        m = cast("Any", OrgFeatureFlag)
        stmt = select(OrgFeatureFlag).where(m.org_id == self.org_id)
        result = await self.session.execute(stmt)
        return {row.flag_name: row.enabled for row in result.scalars().all()}

    async def set_override(self, flag_name: str, enabled: bool) -> OrgFeatureFlag:
        existing = await self._get(flag_name)
        if existing is not None:
            existing.enabled = enabled
            await self.session.flush()
            return existing
        flag = OrgFeatureFlag(org_id=self.org_id, flag_name=flag_name, enabled=enabled)
        self.session.add(flag)
        await self.session.flush()
        return flag

    async def delete_override(self, flag_name: str) -> None:
        existing = await self._get(flag_name)
        if existing is not None:
            await self.session.delete(existing)
            await self.session.flush()

    async def _get(self, flag_name: str) -> OrgFeatureFlag | None:
        m = cast("Any", OrgFeatureFlag)
        stmt = select(OrgFeatureFlag).where(m.org_id == self.org_id, m.flag_name == flag_name)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
