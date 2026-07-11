"""
Business logic for organization creation, the invitation flow, and membership
management (T-026). The "cannot demote the last owner" invariant lives here,
not in the repository or router -- it requires reading the org's current
owner count, which is a business rule, not a data-access concern.
"""

import re
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import jobs
from app.core.config import get_settings
from app.core.exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.models._mixins import utcnow
from app.modules.audit.service import AuditService
from app.modules.auth.models import User
from app.modules.auth.permissions import ROLE_PERMISSIONS, MemberRole, Permission
from app.modules.auth.utils import hash_token, sign_url_token, verify_url_token
from app.modules.notifications.service import NotificationService
from app.modules.tenancy.models import Invitation, Membership, Organization
from app.modules.tenancy.repository import (
    InvitationRepository,
    MembershipRepository,
    OrganizationRepository,
)

_INVITATION_TTL = timedelta(hours=72)
_INVITATION_TTL_SECONDS = int(_INVITATION_TTL.total_seconds())
_SLUG_DISALLOWED = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    slug = _SLUG_DISALLOWED.sub("-", name.lower()).strip("-")
    return slug or "org"


class TenancyService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._orgs = OrganizationRepository(session)
        self._memberships = MembershipRepository(session)
        self._invitations = InvitationRepository(session)
        self._audit = AuditService(session)

    async def create_org(self, user: User, name: str) -> Organization:
        slug = await self._unique_slug(name)
        org = await self._orgs.create(name=name, slug=slug)
        await self._memberships.create(user_id=user.id, org_id=org.id, role=MemberRole.OWNER)
        return org

    async def _unique_slug(self, name: str) -> str:
        base = _slugify(name)
        slug = base
        suffix = 2
        while await self._orgs.get_by_slug(slug) is not None:
            slug = f"{base}-{suffix}"
            suffix += 1
        return slug

    async def invite(
        self, org_id: UUID, inviter: Membership, email: str, role: MemberRole, redis: Redis
    ) -> None:
        # No user lookup -- the invitee may not have an account yet.
        invitation_id = uuid4()
        plain_token = sign_url_token({"invitation_id": str(invitation_id)}, _INVITATION_TTL_SECONDS)
        await self._invitations.create(
            invitation_id=invitation_id,
            org_id=org_id,
            email=email,
            role=role,
            token_hash=hash_token(plain_token),
            invited_by=inviter.user_id,
            expires_at=utcnow() + _INVITATION_TTL,
        )
        org = await self._orgs.get_by_id(org_id)
        if org is None:
            raise NotFoundError("errors.org_not_found", fallback="Organization not found")
        await self._enqueue_invitation_email(org.name, email, plain_token)
        # Confirms to the inviter (not the invitee, who may have no account
        # yet to notify in-app) that the invite went out -- T-160's
        # notifications happy path checks this lands in the inviter's bell
        # within 1s via the in-app/SSE channel.
        await NotificationService(self._session, redis).send(
            user_id=inviter.user_id,
            org_id=org_id,
            type="member_invited",
            title="Invitation sent",
            body=f"You invited {email} to join {org.name}.",
        )

    async def accept_invitation(self, token: str, user: User) -> Membership:
        # Two independent checks: the itsdangerous signature + embedded TTL
        # (cryptographic, survives DB tampering) and the DB row's own
        # accepted_at/expires_at (catches single-use and any drift between
        # the two TTLs). Either one failing means the invitation is invalid.
        verify_url_token(token, max_age=_INVITATION_TTL_SECONDS)
        invitation = await self._invitations.get_by_token_hash(hash_token(token))
        if invitation is None:
            raise AuthenticationError(
                "errors.invalid_invitation", fallback="Invalid or expired invitation"
            )

        await self._invitations.mark_accepted(invitation.id)
        return await self._memberships.create(
            user_id=user.id,
            org_id=invitation.org_id,
            role=invitation.role,
            invited_by=invitation.invited_by,
        )

    async def change_role(
        self, actor_membership: Membership, target_membership_id: UUID, new_role: MemberRole
    ) -> Membership:
        if Permission.MEMBERS_MANAGE not in ROLE_PERMISSIONS[actor_membership.role]:
            raise PermissionDeniedError(
                "errors.missing_permission",
                fallback="Missing permission: members:manage",
                params={"permission": "members:manage"},
            )

        target = await self._memberships.get_by_id(target_membership_id)
        # Org match, not just existence: target_membership_id is caller-
        # supplied, so without this an actor authorized in their own org
        # could change the role of a membership row in a different org —
        # exactly the cross-tenant access §0.2 forbids. A membership in
        # another org is indistinguishable from a missing one here.
        if target is None or target.org_id != actor_membership.org_id:
            raise NotFoundError("errors.membership_not_found", fallback="Membership not found")

        if target.role == MemberRole.OWNER and new_role != MemberRole.OWNER:
            owner_count = await self._memberships.count_by_org_and_role(
                target.org_id, MemberRole.OWNER
            )
            if owner_count <= 1:
                raise ConflictError(
                    "errors.cannot_demote_last_owner",
                    fallback="Cannot demote the last owner of an organization",
                )

        old_role = target.role
        updated = await self._memberships.set_role(target.id, new_role)
        await self._audit.log(
            action="membership.role_changed",
            resource_type="membership",
            resource_id=str(target.id),
            org_id=actor_membership.org_id,
            actor_id=actor_membership.user_id,
            diff={"old_role": old_role.value, "new_role": new_role.value},
        )
        return updated

    async def remove_member(self, actor_membership: Membership, target_membership_id: UUID) -> None:
        if Permission.MEMBERS_MANAGE not in ROLE_PERMISSIONS[actor_membership.role]:
            raise PermissionDeniedError(
                "errors.missing_permission",
                fallback="Missing permission: members:manage",
                params={"permission": "members:manage"},
            )

        target = await self._memberships.get_by_id(target_membership_id)
        if target is None or target.org_id != actor_membership.org_id:  # see change_role
            raise NotFoundError("errors.membership_not_found", fallback="Membership not found")

        if target.role == MemberRole.OWNER:
            owner_count = await self._memberships.count_by_org_and_role(
                target.org_id, MemberRole.OWNER
            )
            if owner_count <= 1:
                raise ConflictError(
                    "errors.cannot_remove_last_owner",
                    fallback="Cannot remove the last owner of an organization",
                )

        await self._memberships.remove(target.id)

    async def get_membership_or_raise(
        self, org_id: UUID, user_id: UUID
    ) -> tuple[Organization, Membership]:
        # Mirrors app.core.dependencies.get_current_org, but keyed off a path
        # parameter rather than the X-Org-ID header -- see router.py's
        # module docstring for why /orgs/{org_id}/... must resolve this way.
        org = await self._orgs.get_by_id(org_id)
        membership = await self._memberships.get_by_user_and_org(user_id, org_id)
        if org is None or membership is None:
            raise PermissionDeniedError(
                "errors.not_org_member", fallback="Not a member of this organization"
            )
        return org, membership

    async def list_orgs_for_user(self, user_id: UUID) -> list[Organization]:
        return await self._orgs.list_for_user(user_id)

    async def update_org(self, org_id: UUID, **fields: Any) -> Organization:  # noqa: ANN401 — forwarded to OrganizationRepository.update
        return await self._orgs.update(org_id, **fields)

    async def list_members(
        self, org_id: UUID, after: UUID | None, limit: int
    ) -> tuple[list[Membership], UUID | None]:
        return await self._memberships.list_for_org(org_id, after=after, limit=limit)

    async def preview_invitation(self, token: str) -> tuple[Invitation, Organization]:
        # Read-only counterpart to accept_invitation: verifies the same two
        # checks (signature+TTL, then the DB row) but never marks the
        # invitation used, since this backs the public "preview" endpoint a
        # browser hits before the invitee has even logged in.
        verify_url_token(token, max_age=_INVITATION_TTL_SECONDS)
        invitation = await self._invitations.get_by_token_hash(hash_token(token))
        if invitation is None:
            raise AuthenticationError(
                "errors.invalid_invitation", fallback="Invalid or expired invitation"
            )
        org = await self._orgs.get_by_id(invitation.org_id)
        if org is None:
            raise AuthenticationError(
                "errors.invalid_invitation", fallback="Invalid or expired invitation"
            )
        return invitation, org

    async def _enqueue_invitation_email(self, org_name: str, email: str, token: str) -> None:
        settings = get_settings()
        await jobs.enqueue_job(
            "send_email",
            to=email,
            template="invitation",
            context={
                "org_name": org_name,
                # T-220: must match the frontend's actual route
                # (apps/web/src/routes/accept-invitation.$token.tsx), not the
                # backend's own GET /api/v1/invitations/{token} preview
                # endpoint path -- confirmed via a real Playwright run that
                # the old "/invitations/{token}" link 404'd in the SPA (no
                # such route was ever defined there).
                "accept_url": f"{settings.FRONTEND_URL}/accept-invitation/{token}",
            },
        )
