"""
DB access for the tenancy module: Organization, Membership, and Invitation
(T-026 extends the read-only Organization/Membership lookups T-021's
dependencies needed with org creation, membership management, and the
invitation flow).

Like app.modules.auth.repository, these are bespoke classes rather than
BaseRepository subclasses: Organization isn't itself org-scoped (it *is* the
org), Membership has no SoftDeleteMixin, and Invitation is looked up by a
public token before any org context exists -- none of the three satisfy
BaseRepository's `_OrgScopedRecord` protocol.
"""

from collections.abc import Collection
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.repository import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.models._mixins import utcnow
from app.modules.auth.permissions import MemberRole
from app.modules.tenancy.models import Invitation, Membership, Organization


class OrganizationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, org_id: UUID) -> Organization | None:
        m = cast("Any", Organization)
        stmt = select(Organization).where(m.id == org_id, m.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> Organization | None:
        # Not org-scoped by design -- this is the public lookup used while
        # generating a unique slug, before the caller has any org context.
        m = cast("Any", Organization)
        stmt = select(Organization).where(m.slug == slug, m.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(self, *, name: str, slug: str) -> Organization:
        org = Organization(name=name, slug=slug)
        self.session.add(org)
        await self.session.flush()
        return org

    async def list_for_user(self, user_id: UUID) -> list[Organization]:
        # Joins through Membership rather than living on MembershipRepository
        # -- the return type is Organization, so it belongs with the other
        # Organization reads. Unbounded (no cursor): the collection this
        # paginates over is "orgs one user belongs to", not an org-scoped
        # table §0.3 is guarding against unbounded growth of.
        m = cast("Any", Organization)
        mem = cast("Any", Membership)
        stmt = (
            select(Organization)
            .join(Membership, mem.org_id == m.id)
            .where(mem.user_id == user_id, m.deleted_at.is_(None))
            .order_by(m.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_with_stripe_customer(
        self, *, limit: int, after_id: UUID | None
    ) -> list[Organization]:
        """One page of orgs that exist in Stripe, ordered by id.

        Keyset pagination on the primary key rather than OFFSET: the caller
        (app.workers.jobs.billing.reconcile_subscriptions) walks every page
        and makes a Stripe API call per org, so a page can be minutes old by
        the time the next one is asked for -- an OFFSET would then skip or
        repeat rows as orgs are created underneath it.
        """
        m = cast("Any", Organization)
        stmt = select(Organization).where(m.stripe_customer_id.is_not(None))
        if after_id is not None:
            stmt = stmt.where(m.id > after_id)
        stmt = stmt.order_by(m.id).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_ids(self, org_ids: Collection[UUID]) -> list[Organization]:
        # T-243: the batch counterpart to get_by_id, for callers holding a
        # page of rows that each name an org (the admin audit feed, the admin
        # user detail's membership list). Those org ids aren't ORM
        # relationships -- Membership/AuditEvent carry a bare UUID column, no
        # `Relationship` to eager-load -- so §0.3's selectinload isn't
        # available here; one `IN` query per page is the same shape of fix.
        if not org_ids:
            return []
        m = cast("Any", Organization)
        stmt = select(Organization).where(m.id.in_(set(org_ids)), m.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_or_raise(self, org_id: UUID) -> Organization:
        org = await self.get_by_id(org_id)
        if org is None:
            raise NotFoundError("errors.org_not_found", fallback="Organization not found")
        return org

    async def update(self, org_id: UUID, **kwargs: Any) -> Organization:  # noqa: ANN401 — see core.repository.BaseRepository.update
        org = await self.get_or_raise(org_id)
        for key, value in kwargs.items():
            setattr(org, key, value)
        await self.session.flush()
        return org


class MembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, membership_id: UUID) -> Membership | None:
        m = cast("Any", Membership)
        stmt = select(Membership).where(m.id == membership_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_user_and_org(self, user_id: UUID, org_id: UUID) -> Membership | None:
        m = cast("Any", Membership)
        stmt = select(Membership).where(m.user_id == user_id, m.org_id == org_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(
        self, *, user_id: UUID, org_id: UUID, role: MemberRole, invited_by: UUID | None = None
    ) -> Membership:
        membership = Membership(user_id=user_id, org_id=org_id, role=role, invited_by=invited_by)
        self.session.add(membership)
        await self.session.flush()
        return membership

    async def list_for_org(
        self, org_id: UUID, after: UUID | None = None, limit: int = DEFAULT_PAGE_SIZE
    ) -> tuple[list[Membership], UUID | None]:
        # Cursor pagination per §0.3 -- mirrors BaseRepository.list_cursor,
        # but ordered/filtered by hand since Membership isn't a
        # BaseRepository subclass (see module docstring).
        capped_limit = min(limit, MAX_PAGE_SIZE)
        m = cast("Any", Membership)
        stmt = (
            select(Membership)
            .where(m.org_id == org_id)
            .order_by(m.created_at.desc(), m.id.desc())
            .limit(capped_limit + 1)
        )
        if after is not None:
            anchor = await self.get_by_id(after)
            if anchor is not None:
                stmt = stmt.where(tuple_(m.created_at, m.id) < (anchor.created_at, anchor.id))
        result = await self.session.execute(stmt)
        items = list(result.scalars().all())
        page = items[:capped_limit]
        # `page` guards against a non-positive capped_limit: items can still
        # be non-empty (.limit(capped_limit + 1) == .limit(1) when
        # capped_limit is 0), but `page` is then empty -- page[-1] would
        # raise IndexError.
        next_cursor = page[-1].id if page and len(items) > capped_limit else None
        return page, next_cursor

    async def list_for_user(self, user_id: UUID) -> list[Membership]:
        # T-141: admin user-detail "memberships" list. Unbounded like
        # OrganizationRepository.list_for_user just above -- "orgs/memberships
        # one user belongs to" is bounded by how many orgs a person can
        # realistically join, not a table §0.3 is guarding unbounded growth
        # of.
        m = cast("Any", Membership)
        stmt = select(Membership).where(m.user_id == user_id).order_by(m.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def set_role(self, membership_id: UUID, role: MemberRole) -> Membership:
        membership = await self._get_or_raise(membership_id)
        membership.role = role
        await self.session.flush()
        return membership

    async def remove(self, membership_id: UUID) -> None:
        # Membership has no SoftDeleteMixin (T-025) -- removing a member is a
        # hard delete, not a tombstone.
        membership = await self._get_or_raise(membership_id)
        await self.session.delete(membership)
        await self.session.flush()

    async def count_by_org_and_role(self, org_id: UUID, role: MemberRole) -> int:
        m = cast("Any", Membership)
        stmt = (
            select(func.count()).select_from(Membership).where(m.org_id == org_id, m.role == role)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def count_by_org(self, org_id: UUID) -> int:
        # T-141: admin org-detail "member count" -- every role, not one at a
        # time like count_by_org_and_role above.
        m = cast("Any", Membership)
        stmt = select(func.count()).select_from(Membership).where(m.org_id == org_id)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def _get_or_raise(self, membership_id: UUID) -> Membership:
        membership = await self.get_by_id(membership_id)
        if membership is None:
            raise NotFoundError("errors.membership_not_found", fallback="Membership not found")
        return membership


class InvitationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(  # noqa: PLR0913 -- matches the T-026 spec's exact field set
        self,
        *,
        invitation_id: UUID,
        org_id: UUID,
        email: str,
        role: MemberRole,
        token_hash: str,
        invited_by: UUID,
        expires_at: datetime,
    ) -> Invitation:
        invitation = Invitation(
            id=invitation_id,
            org_id=org_id,
            email=email,
            role=role,
            token_hash=token_hash,
            invited_by=invited_by,
            expires_at=expires_at,
        )
        self.session.add(invitation)
        await self.session.flush()
        return invitation

    async def list_pending_for_org(
        self, org_id: UUID, after: UUID | None = None, limit: int = DEFAULT_PAGE_SIZE
    ) -> tuple[list[Invitation], UUID | None]:
        # "Pending" is accepted_at IS NULL only -- expired-but-unaccepted rows
        # stay in the list deliberately. The caller is the org's admin screen,
        # and an expired invite is exactly the row someone needs to see in
        # order to re-send it; hiding it would make an unanswered invitation
        # silently vanish. InvitationPublic carries expires_at so the UI can
        # mark it expired.
        #
        # Cursor pagination per §0.3, same hand-rolled shape as
        # MembershipRepository.list_for_org above (Invitation isn't a
        # BaseRepository subclass either -- see the module docstring).
        capped_limit = min(limit, MAX_PAGE_SIZE)
        m = cast("Any", Invitation)
        stmt = (
            select(Invitation)
            .where(m.org_id == org_id, m.accepted_at.is_(None))
            .order_by(m.created_at.desc(), m.id.desc())
            .limit(capped_limit + 1)
        )
        if after is not None:
            # Scoped to this org, so a cursor naming another org's invitation
            # is treated as "no cursor" rather than leaking its position
            # (mirrors NotificationRepository.list_for_user's anchor lookup).
            anchor_stmt = select(Invitation).where(m.id == after, m.org_id == org_id)
            anchor = (await self.session.execute(anchor_stmt)).scalar_one_or_none()
            if anchor is not None:
                stmt = stmt.where(tuple_(m.created_at, m.id) < (anchor.created_at, anchor.id))
        result = await self.session.execute(stmt)
        items = list(result.scalars().all())
        page = items[:capped_limit]
        # `page` guards a non-positive capped_limit -- see list_for_org.
        next_cursor = page[-1].id if page and len(items) > capped_limit else None
        return page, next_cursor

    async def get_by_token_hash(self, token_hash: str) -> Invitation | None:
        m = cast("Any", Invitation)
        stmt = select(Invitation).where(
            m.token_hash == token_hash,
            m.accepted_at.is_(None),
            m.expires_at > utcnow(),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def mark_accepted(self, invitation_id: UUID) -> None:
        m = cast("Any", Invitation)
        stmt = select(Invitation).where(m.id == invitation_id)
        result = await self.session.execute(stmt)
        invitation = result.scalar_one_or_none()
        if invitation is None:
            raise NotFoundError("errors.invitation_not_found", fallback="Invitation not found")
        invitation.accepted_at = utcnow()
        await self.session.flush()
