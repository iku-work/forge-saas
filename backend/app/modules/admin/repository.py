"""
DB access for T-141's admin org directory. Like AdminFeatureFlagService
(T-122), which already reaches directly into
app.modules.feature_flags.repository from admin's service layer, this module
is allowed to query across tenancy and billing directly -- the normal
"cross-module access goes through services" rule (§0.1) exists so ordinary
feature modules don't couple to each other; the admin/support surface is by
definition a cross-tenant, cross-module read model that no single module's
own repository can provide.

list_orgs guards every billing-only join behind
app.core.module_loader.is_enabled("billing") (mirrors T-065's established
billing-disabled fallback pattern) since the subscription/plan tables don't
exist at all in a billing-disabled product -- joining them unconditionally
would break every admin org listing the moment billing is toggled off.
"""

from typing import Any, cast
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.module_loader import is_enabled
from app.core.repository import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.modules.billing.models import Plan, Subscription
from app.modules.tenancy.models import Organization

# (org, plan_slug, subscription_status) -- the shape AdminDirectoryService
# needs to build AdminOrgSummary without a second query per row.
OrgListRow = tuple[Organization, "str | None", "str | None"]


class AdminOrgRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_orgs(
        self,
        after: UUID | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        *,
        q: str | None = None,
        plan_slug: str | None = None,
        status: str | None = None,
    ) -> tuple[list[OrgListRow], UUID | None]:
        capped_limit = min(limit, MAX_PAGE_SIZE)
        m = cast("Any", Organization)
        billing_on = is_enabled("billing")

        if (plan_slug is not None or status is not None) and not billing_on:
            # Billing disabled: subscription/plan tables don't exist, so a
            # plan/status filter can never match anything real -- empty page,
            # not a query against a missing table.
            return [], None

        if billing_on:
            sub = cast("Any", Subscription)
            plan = cast("Any", Plan)
            stmt = (
                select(Organization, plan.slug, sub.status)
                .join(Subscription, sub.org_id == m.id, isouter=True)
                .join(Plan, plan.id == sub.plan_id, isouter=True)
                .where(m.deleted_at.is_(None))
            )
            if status is not None:
                stmt = stmt.where(sub.status == status)
            if plan_slug is not None:
                stmt = stmt.where(plan.slug == plan_slug)
        else:
            stmt = select(Organization).where(m.deleted_at.is_(None))

        if q is not None:
            like = f"%{q}%"
            stmt = stmt.where((m.slug.ilike(like)) | (m.name.ilike(like)))

        stmt = stmt.order_by(m.created_at.desc(), m.id.desc()).limit(capped_limit + 1)
        if after is not None:
            anchor = await self.session.get(Organization, after)
            if anchor is not None and anchor.deleted_at is None:
                stmt = stmt.where(tuple_(m.created_at, m.id) < (anchor.created_at, anchor.id))

        result = await self.session.execute(stmt)
        entries: list[OrgListRow]
        if billing_on:
            entries = [(row[0], row[1], row[2]) for row in result.all()]
        else:
            entries = [(org, None, None) for org in result.scalars().all()]

        page = entries[:capped_limit]
        next_cursor = page[-1][0].id if page and len(entries) > capped_limit else None
        return page, next_cursor
