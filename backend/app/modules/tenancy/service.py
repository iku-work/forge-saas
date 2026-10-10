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
from app.core.exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.module_loader import is_enabled
from app.core.urls import frontend_url
from app.models._mixins import utcnow
from app.modules.audit.service import AuditService
from app.modules.auth.models import User
from app.modules.auth.permissions import ROLE_PERMISSIONS, MemberRole, Permission
from app.modules.auth.repository import UserRepository
from app.modules.auth.service import AuthService
from app.modules.auth.utils import hash_token, sign_url_token, verify_url_token
from app.modules.notifications.service import NotificationService
from app.modules.tenancy.models import Invitation, Membership, Organization
from app.modules.tenancy.repository import (
    InvitationRepository,
    MembershipRepository,
    OrganizationRepository,
)

# T-320: imported at module scope rather than lazily, unlike build_router()'s
# router imports. The distinction is what the import costs: a router pulls in
# a whole HTTP surface, while these two are an enum, a few pydantic models and
# a repository. `webhooks.emit` is safe to call with the module off -- it
# returns immediately -- so every call site below is unconditional except the
# one that would otherwise need an extra query to build its payload.
from app.modules.webhooks import service as webhooks
from app.modules.webhooks.events import EventType, MemberPayload, OrgPayload

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
        # The invitation row itself is keyed on the email, not on a user --
        # the invitee may not have an account yet. (T-233's _invitee_locale
        # does look one up, but only to pick a language, and treats "no such
        # user" as an ordinary case rather than an error.)
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
        await self._enqueue_invitation_email(
            org.name, email, plain_token, await self._invitee_locale(email, inviter.user_id)
        )
        # Confirms to the inviter (not the invitee, who may have no account
        # yet to notify in-app) that the invite went out -- T-160's
        # notifications happy path checks this lands in the inviter's bell
        # within 1s via the in-app/SSE channel.
        await NotificationService(self._session, redis).send(
            user_id=inviter.user_id,
            org_id=org_id,
            type="member_invited",
            params={"email": email, "org_name": org.name},
        )
        await webhooks.emit(
            self._session,
            org_id,
            EventType.MEMBER_INVITED,
            MemberPayload(email=email, role=role.value),
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
        membership = await self._memberships.create(
            user_id=user.id,
            org_id=invitation.org_id,
            role=invitation.role,
            invited_by=invitation.invited_by,
        )
        await webhooks.emit(
            self._session,
            invitation.org_id,
            EventType.MEMBER_JOINED,
            MemberPayload(
                membership_id=membership.id,
                user_id=user.id,
                email=user.email,
                role=membership.role.value,
            ),
        )
        return membership

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
        # Guarded, unlike the call sites above: the payload needs the member's
        # email, which is a row this method does not otherwise read. A product
        # with the module off must not pay for a query it will never use.
        if is_enabled("outbound_webhooks"):
            member = await UserRepository(self._session).get_by_id(target.user_id)
            await webhooks.emit(
                self._session,
                target.org_id,
                EventType.MEMBER_ROLE_CHANGED,
                MemberPayload(
                    membership_id=target.id,
                    user_id=target.user_id,
                    email=member.email if member else "",
                    role=new_role.value,
                ),
            )
        return updated

    async def remove_member(self, actor_membership: Membership, target_membership_id: UUID) -> UUID:
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

        # Emitted before the row is removed, and guarded for the same reason
        # as change_role's: the email is an extra read.
        if is_enabled("outbound_webhooks"):
            member = await UserRepository(self._session).get_by_id(target.user_id)
            await webhooks.emit(
                self._session,
                target.org_id,
                EventType.MEMBER_REMOVED,
                MemberPayload(
                    membership_id=target.id,
                    user_id=target.user_id,
                    email=member.email if member else "",
                    role=target.role.value,
                ),
            )
        await self._memberships.remove(target.id)
        # The removed member's user id, so the router can bust that user's
        # cache (T-240) -- the membership row is gone by the time it returns.
        return target.user_id

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

    async def get_org(self, org_id: UUID) -> Organization | None:
        """The organization row on its own, with no membership check.

        T-311: `get_org`'s route now proves membership through
        `require_scope` (which serves both a session and an API key), so the
        route needs the row and nothing else. get_membership_or_raise above
        is still the right call for every route that needs the Membership
        too -- this is deliberately not a replacement for it, and a caller
        that skips both has no tenant-isolation check at all.
        """
        return await self._orgs.get_by_id(org_id)

    async def list_orgs_for_user(self, user_id: UUID) -> list[Organization]:
        return await self._orgs.list_for_user(user_id)

    async def update_org(self, org_id: UUID, **fields: Any) -> Organization:  # noqa: ANN401 — forwarded to OrganizationRepository.update
        org = await self._orgs.update(org_id, **fields)
        await webhooks.emit(
            self._session,
            org_id,
            EventType.ORG_UPDATED,
            OrgPayload(org_id=org.id, name=org.name, slug=org.slug),
        )
        return org

    async def list_members(
        self, org_id: UUID, after: UUID | None, limit: int, email_query: str | None = None
    ) -> tuple[list[Membership], UUID | None]:
        return await self._memberships.list_for_org(
            org_id, after=after, limit=limit, email_query=email_query
        )

    async def count_members(self, org_id: UUID, email_query: str | None = None) -> int:
        return await self._memberships.count_by_org(org_id, email_query=email_query)

    async def users_for_memberships(self, memberships: list[Membership]) -> dict[UUID, User]:
        """The users behind a page of memberships, in one query (T-243)."""
        return await AuthService(self._session).users_by_ids(
            {membership.user_id for membership in memberships}
        )

    async def member_avatar_key(self, org_id: UUID, membership_id: UUID) -> str:
        """The object key of a fellow member's uploaded avatar.

        404 for a membership outside `org_id` (the same answer as one that
        does not exist, so another org's ids reveal nothing), for a deleted
        user, and for a member with no uploaded avatar.
        """
        membership = await self._memberships.get_by_id(membership_id)
        if membership is None or membership.org_id != org_id:
            raise NotFoundError("errors.membership_not_found", fallback="Membership not found")
        user = (await AuthService(self._session).users_by_ids({membership.user_id})).get(
            membership.user_id
        )
        if user is None or user.avatar_object_key is None:
            raise NotFoundError("errors.avatar_not_found", fallback="No uploaded avatar.")
        return user.avatar_object_key

    async def list_pending_invitations(
        self, org_id: UUID, after: UUID | None, limit: int
    ) -> tuple[list[Invitation], UUID | None]:
        return await self._invitations.list_pending_for_org(org_id, after=after, limit=limit)

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

    async def _invitee_locale(self, email: str, inviter_id: UUID) -> str | None:
        """T-233: which language the invitation email -- and the locale
        segment of its accept link -- should be in.

        An invitee who already has an account gets their own stored
        preference, exactly like every other transactional email (§0.11).
        One who doesn't yet gets the *inviter's*, which is the only signal
        available and a much better guess than the product default: they are
        being invited into the inviter's organization.
        """
        users = UserRepository(self._session)
        invitee = await users.get_by_email(email)
        if invitee is not None:
            return invitee.locale
        inviter = await users.get_by_id(inviter_id)
        return inviter.locale if inviter is not None else None

    async def _enqueue_invitation_email(
        self, org_name: str, email: str, token: str, locale: str | None
    ) -> None:
        await jobs.enqueue_job(
            "send_email",
            to=email,
            template="invitation",
            locale=locale,
            context={
                "org_name": org_name,
                # T-220: must match the frontend's actual route
                # (apps/web/src/routes/$locale.accept-invitation.$token.tsx),
                # not the backend's own GET /api/v1/invitations/{token}
                # preview endpoint path -- confirmed via a real Playwright run
                # that the old "/invitations/{token}" link 404'd in the SPA (no
                # such route was ever defined there).
                # T-233: locale-prefixed. A pre-T-233 link without the segment
                # still works (the SPA redirects it), but only after a
                # client-side round trip, and lands the invitee in whatever
                # locale *their browser* resolves to rather than the one this
                # email is written in.
                "accept_url": frontend_url(f"/accept-invitation/{token}", locale),
            },
        )
