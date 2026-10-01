"""
Verifies T-026 (tenancy service): org creation atomically creates an OWNER
membership and a collision-safe slug, the invitation flow is single-use and
time-bound, and changing a member's role enforces both the MEMBERS_MANAGE
permission and the "never demote the last owner" invariant. Uses the same
real-Postgres testcontainers pattern as test_auth_service.py.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from app.core.exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.modules.audit.models import AuditEvent
from app.modules.auth.models import User
from app.modules.auth.permissions import MemberRole
from app.modules.auth.utils import hash_token, sign_url_token
from app.modules.tenancy.models import Invitation, Membership, Organization
from app.modules.tenancy.repository import (
    InvitationRepository,
    MembershipRepository,
    OrganizationRepository,
)
from app.modules.tenancy.service import _INVITATION_TTL_SECONDS, TenancyService, _slugify


def _future(hours: int = 72) -> datetime:
    return datetime.now(UTC) + timedelta(hours=hours)


def _past(hours: int = 1) -> datetime:
    return datetime.now(UTC) - timedelta(hours=hours)


@pytest.fixture
def service(session: AsyncSession) -> TenancyService:
    return TenancyService(session)


@pytest.fixture
def org_repo(session: AsyncSession) -> OrganizationRepository:
    return OrganizationRepository(session)


@pytest.fixture
def membership_repo(session: AsyncSession) -> MembershipRepository:
    return MembershipRepository(session)


@pytest.fixture
def invitation_repo(session: AsyncSession) -> InvitationRepository:
    return InvitationRepository(session)


@pytest.fixture
async def user(session: AsyncSession) -> User:
    u = User(email=f"user-{uuid4()}@example.com")
    session.add(u)
    await session.flush()
    return u


@pytest.fixture
async def org(session: AsyncSession) -> Organization:
    o = Organization(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    session.add(o)
    await session.flush()
    return o


async def _issue_invitation(  # noqa: PLR0913 -- test helper mirrors TenancyService.invite's full field set
    invitation_repo: InvitationRepository,
    org: Organization,
    inviter: User,
    *,
    email: str = "invitee@example.com",
    role: MemberRole = MemberRole.MEMBER,
    expires_at: datetime | None = None,
) -> str:
    """Mirrors what TenancyService.invite stores, but hands back the plain token directly."""
    invitation_id = uuid4()
    plain_token = sign_url_token({"invitation_id": str(invitation_id)}, _INVITATION_TTL_SECONDS)
    await invitation_repo.create(
        invitation_id=invitation_id,
        org_id=org.id,
        email=email,
        role=role,
        token_hash=hash_token(plain_token),
        invited_by=inviter.id,
        expires_at=expires_at or _future(),
    )
    return plain_token


# --- _slugify ---------------------------------------------------------------


def test_slugify_lowercases_and_hyphenates() -> None:
    assert _slugify("Acme Corp") == "acme-corp"


def test_slugify_falls_back_to_org_for_unslugifiable_name() -> None:
    assert _slugify("!!!") == "org"


# --- create_org --------------------------------------------------------------


async def test_create_org_creates_org_and_owner_membership(
    service: TenancyService, user: User, membership_repo: MembershipRepository
) -> None:
    org = await service.create_org(user, "Acme Corp")

    assert org.name == "Acme Corp"
    membership = await membership_repo.get_by_user_and_org(user.id, org.id)
    assert membership is not None
    assert membership.role == MemberRole.OWNER


async def test_create_org_generates_slug_from_name(service: TenancyService, user: User) -> None:
    org = await service.create_org(user, "Acme Corp")

    assert org.slug == "acme-corp"


async def test_create_org_slug_collision_gets_numeric_suffix(
    service: TenancyService, user: User, session: AsyncSession
) -> None:
    first = await service.create_org(user, "Acme Corp")

    other_user = User(email=f"other-{uuid4()}@example.com")
    session.add(other_user)
    await session.flush()
    second = await service.create_org(other_user, "Acme Corp")

    assert first.slug == "acme-corp"
    assert second.slug == "acme-corp-2"


# --- invite -------------------------------------------------------------------


async def test_invite_creates_invitation_with_correct_fields(
    service: TenancyService,
    org: Organization,
    user: User,
    session: AsyncSession,
    redis: Redis,
) -> None:
    inviter = Membership(user_id=user.id, org_id=org.id, role=MemberRole.OWNER)

    await service.invite(org.id, inviter, "newcomer@example.com", MemberRole.ADMIN, redis)

    result = await session.execute(
        select(Invitation).where(
            Invitation.org_id == org.id, Invitation.email == "newcomer@example.com"
        )
    )
    rows = result.scalars().all()
    assert len(rows) == 1
    invitation = rows[0]
    assert invitation.role == MemberRole.ADMIN
    assert invitation.invited_by == user.id
    assert invitation.accepted_at is None


async def test_invite_raises_not_found_for_soft_deleted_org(
    service: TenancyService,
    org: Organization,
    user: User,
    session: AsyncSession,
    redis: Redis,
) -> None:
    # A literally nonexistent org_id would already fail _invitations.create's
    # FK constraint before reaching this branch -- soft-deleting a real org
    # is the only way org_id passes that insert while OrganizationRepository
    # .get_by_id (which filters deleted_at IS NULL) still reports it missing.
    org.deleted_at = datetime.now(UTC)
    session.add(org)
    await session.flush()
    inviter = Membership(user_id=user.id, org_id=org.id, role=MemberRole.OWNER)

    with pytest.raises(NotFoundError):
        await service.invite(org.id, inviter, "nobody@example.com", MemberRole.MEMBER, redis)


# --- invite email locale (T-233) ----------------------------------------------


async def _invite_email(  # noqa: PLR0913 -- mirrors TenancyService.invite's own argument set
    service: TenancyService,
    org: Organization,
    inviter: User,
    invitee_email: str,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
    redis: Redis,
) -> dict[str, Any]:
    membership = Membership(user_id=inviter.id, org_id=org.id, role=MemberRole.OWNER)
    await service.invite(org.id, membership, invitee_email, MemberRole.MEMBER, redis)
    # Not enqueued_jobs[-1]: invite() also notifies the inviter (T-160), which
    # enqueues its own send_email job after this one.
    return next(
        kwargs
        for function, kwargs in enqueued_jobs
        if function == "send_email" and kwargs["to"] == invitee_email
    )


async def test_invite_email_uses_an_existing_invitees_own_locale(  # noqa: PLR0913 -- redis on top of the usual service/org/user/session/enqueued_jobs set
    service: TenancyService,
    org: Organization,
    user: User,
    session: AsyncSession,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
    redis: Redis,
) -> None:
    """Per-recipient localisation (§0.11): someone who already has an account
    gets invited in *their* language, not the inviter's."""
    user.locale = "de"
    invitee = User(email="existing-invitee@example.com", locale="fi")
    session.add_all([user, invitee])
    await session.flush()

    kwargs = await _invite_email(service, org, user, invitee.email, enqueued_jobs, redis)

    assert kwargs["locale"] == "fi"
    assert "/fi/accept-invitation/" in kwargs["context"]["accept_url"]


async def test_invite_email_falls_back_to_the_inviters_locale(  # noqa: PLR0913 -- redis on top of the usual service/org/user/session/enqueued_jobs set
    service: TenancyService,
    org: Organization,
    user: User,
    session: AsyncSession,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
    redis: Redis,
) -> None:
    """An invitee with no account has no preference to honour -- the inviter's
    is the only signal, and a better guess than the product default."""
    user.locale = "de"
    session.add(user)
    await session.flush()

    kwargs = await _invite_email(service, org, user, "brand-new@example.com", enqueued_jobs, redis)

    assert kwargs["locale"] == "de"
    assert "/de/accept-invitation/" in kwargs["context"]["accept_url"]


# --- invite + accept round-trip -----------------------------------------------


async def test_invite_then_accept_round_trip(  # noqa: PLR0913 -- redis on top of the usual service/org/user/session/enqueued_jobs set
    service: TenancyService,
    org: Organization,
    user: User,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
    session: AsyncSession,
    redis: Redis,
) -> None:
    inviter = Membership(user_id=user.id, org_id=org.id, role=MemberRole.OWNER)
    await service.invite(org.id, inviter, "invitee@example.com", MemberRole.MEMBER, redis)

    # Not enqueued_jobs[-1]: invite() also notifies the inviter (T-160's
    # member_invited notification), which enqueues its own send_email job
    # after this one.
    function, kwargs = next(
        (f, k) for f, k in enqueued_jobs if f == "send_email" and k["to"] == "invitee@example.com"
    )
    assert function == "send_email"
    assert kwargs["to"] == "invitee@example.com"
    token = kwargs["context"]["accept_url"].rsplit("/", 1)[-1]

    invitee = User(email="invitee@example.com")
    session.add(invitee)
    await session.flush()
    membership = await service.accept_invitation(token, invitee)

    assert membership.org_id == org.id
    assert membership.user_id == invitee.id
    assert membership.role == MemberRole.MEMBER


async def test_accept_invitation_used_twice_raises_on_second_call(
    service: TenancyService,
    invitation_repo: InvitationRepository,
    org: Organization,
    user: User,
    session: AsyncSession,
) -> None:
    token = await _issue_invitation(invitation_repo, org, user)
    invitee = User(email=f"invitee-{uuid4()}@example.com")
    session.add(invitee)
    await session.flush()
    await service.accept_invitation(token, invitee)

    with pytest.raises(AuthenticationError):
        await service.accept_invitation(token, invitee)


async def test_accept_invitation_expired_raises(
    service: TenancyService,
    invitation_repo: InvitationRepository,
    org: Organization,
    user: User,
    session: AsyncSession,
) -> None:
    token = await _issue_invitation(invitation_repo, org, user, expires_at=_past())
    invitee = User(email=f"invitee-{uuid4()}@example.com")
    session.add(invitee)
    await session.flush()

    with pytest.raises(AuthenticationError):
        await service.accept_invitation(token, invitee)


async def test_accept_invitation_unknown_token_raises(service: TenancyService, user: User) -> None:
    with pytest.raises(AuthenticationError):
        await service.accept_invitation("not-a-real-token", user)


# --- change_role ---------------------------------------------------------------


async def test_change_role_denies_actor_without_members_manage(
    service: TenancyService, membership_repo: MembershipRepository, org: Organization, user: User
) -> None:
    target = await membership_repo.create(user_id=user.id, org_id=org.id, role=MemberRole.MEMBER)
    actor = Membership(user_id=uuid4(), org_id=org.id, role=MemberRole.MEMBER)

    with pytest.raises(PermissionDeniedError):
        await service.change_role(actor, target.id, MemberRole.ADMIN)


async def test_change_role_raises_not_found_for_missing_target(
    service: TenancyService, org: Organization
) -> None:
    actor = Membership(user_id=uuid4(), org_id=org.id, role=MemberRole.OWNER)

    with pytest.raises(NotFoundError):
        await service.change_role(actor, uuid4(), MemberRole.ADMIN)


async def test_change_role_updates_non_owner_target(
    service: TenancyService, membership_repo: MembershipRepository, org: Organization, user: User
) -> None:
    target = await membership_repo.create(user_id=user.id, org_id=org.id, role=MemberRole.MEMBER)
    actor = Membership(user_id=uuid4(), org_id=org.id, role=MemberRole.OWNER)

    updated = await service.change_role(actor, target.id, MemberRole.ADMIN)

    assert updated.role == MemberRole.ADMIN


async def test_change_role_writes_audit_event_with_old_and_new_role(
    service: TenancyService,
    membership_repo: MembershipRepository,
    org: Organization,
    user: User,
    session: AsyncSession,
) -> None:
    target = await membership_repo.create(user_id=user.id, org_id=org.id, role=MemberRole.MEMBER)
    actor = Membership(user_id=uuid4(), org_id=org.id, role=MemberRole.OWNER)

    await service.change_role(actor, target.id, MemberRole.ADMIN)

    event = (
        await session.execute(
            select(AuditEvent).where(AuditEvent.action == "membership.role_changed")
        )
    ).scalar_one()
    assert event.org_id == org.id
    assert event.actor_id == actor.user_id
    assert event.resource_id == str(target.id)
    assert event.diff == {"old_role": "member", "new_role": "admin"}


async def test_change_role_demoting_last_owner_raises_conflict(
    service: TenancyService, membership_repo: MembershipRepository, org: Organization, user: User
) -> None:
    target = await membership_repo.create(user_id=user.id, org_id=org.id, role=MemberRole.OWNER)
    actor = Membership(user_id=uuid4(), org_id=org.id, role=MemberRole.OWNER)

    with pytest.raises(ConflictError):
        await service.change_role(actor, target.id, MemberRole.ADMIN)


async def test_change_role_demoting_one_of_several_owners_succeeds(
    service: TenancyService,
    membership_repo: MembershipRepository,
    org: Organization,
    user: User,
    session: AsyncSession,
) -> None:
    target = await membership_repo.create(user_id=user.id, org_id=org.id, role=MemberRole.OWNER)
    other_owner = User(email=f"other-owner-{uuid4()}@example.com")
    session.add(other_owner)
    await session.flush()
    await membership_repo.create(user_id=other_owner.id, org_id=org.id, role=MemberRole.OWNER)
    actor = Membership(user_id=uuid4(), org_id=org.id, role=MemberRole.OWNER)

    updated = await service.change_role(actor, target.id, MemberRole.ADMIN)

    assert updated.role == MemberRole.ADMIN


async def test_change_role_target_in_different_org_raises_not_found(
    service: TenancyService,
    membership_repo: MembershipRepository,
    org_repo: OrganizationRepository,
    org: Organization,
    user: User,
) -> None:
    other_org = await org_repo.create(name="Other", slug=f"other-{uuid4().hex[:8]}")
    target = await membership_repo.create(
        user_id=user.id, org_id=other_org.id, role=MemberRole.MEMBER
    )
    actor = Membership(user_id=uuid4(), org_id=org.id, role=MemberRole.OWNER)

    with pytest.raises(NotFoundError):
        await service.change_role(actor, target.id, MemberRole.ADMIN)


# --- remove_member -------------------------------------------------------------


async def test_remove_member_denies_actor_without_members_manage(
    service: TenancyService, membership_repo: MembershipRepository, org: Organization, user: User
) -> None:
    target = await membership_repo.create(user_id=user.id, org_id=org.id, role=MemberRole.MEMBER)
    actor = Membership(user_id=uuid4(), org_id=org.id, role=MemberRole.MEMBER)

    with pytest.raises(PermissionDeniedError):
        await service.remove_member(actor, target.id)


async def test_remove_member_raises_not_found_for_missing(
    service: TenancyService, org: Organization
) -> None:
    actor = Membership(user_id=uuid4(), org_id=org.id, role=MemberRole.OWNER)

    with pytest.raises(NotFoundError):
        await service.remove_member(actor, uuid4())


async def test_remove_member_target_in_different_org_raises_not_found(
    service: TenancyService,
    membership_repo: MembershipRepository,
    org_repo: OrganizationRepository,
    org: Organization,
    user: User,
) -> None:
    other_org = await org_repo.create(name="Other", slug=f"other-{uuid4().hex[:8]}")
    target = await membership_repo.create(
        user_id=user.id, org_id=other_org.id, role=MemberRole.MEMBER
    )
    actor = Membership(user_id=uuid4(), org_id=org.id, role=MemberRole.OWNER)

    with pytest.raises(NotFoundError):
        await service.remove_member(actor, target.id)


async def test_remove_member_removes_non_owner_target(
    service: TenancyService, membership_repo: MembershipRepository, org: Organization, user: User
) -> None:
    target = await membership_repo.create(user_id=user.id, org_id=org.id, role=MemberRole.MEMBER)
    actor = Membership(user_id=uuid4(), org_id=org.id, role=MemberRole.OWNER)

    await service.remove_member(actor, target.id)

    assert await membership_repo.get_by_id(target.id) is None


async def test_remove_member_demoting_last_owner_raises_conflict(
    service: TenancyService, membership_repo: MembershipRepository, org: Organization, user: User
) -> None:
    target = await membership_repo.create(user_id=user.id, org_id=org.id, role=MemberRole.OWNER)
    actor = Membership(user_id=uuid4(), org_id=org.id, role=MemberRole.OWNER)

    with pytest.raises(ConflictError):
        await service.remove_member(actor, target.id)


async def test_remove_member_removing_one_of_several_owners_succeeds(
    service: TenancyService,
    membership_repo: MembershipRepository,
    org: Organization,
    user: User,
    session: AsyncSession,
) -> None:
    target = await membership_repo.create(user_id=user.id, org_id=org.id, role=MemberRole.OWNER)
    other_owner = User(email=f"other-owner-{uuid4()}@example.com")
    session.add(other_owner)
    await session.flush()
    await membership_repo.create(user_id=other_owner.id, org_id=org.id, role=MemberRole.OWNER)
    actor = Membership(user_id=uuid4(), org_id=org.id, role=MemberRole.OWNER)

    await service.remove_member(actor, target.id)

    assert await membership_repo.get_by_id(target.id) is None


# --- get_membership_or_raise -----------------------------------------------


async def test_get_membership_or_raise_returns_org_and_membership_for_member(
    service: TenancyService, membership_repo: MembershipRepository, org: Organization, user: User
) -> None:
    await membership_repo.create(user_id=user.id, org_id=org.id, role=MemberRole.MEMBER)

    returned_org, returned_membership = await service.get_membership_or_raise(org.id, user.id)

    assert returned_org.id == org.id
    assert returned_membership.user_id == user.id


async def test_get_membership_or_raise_raises_for_non_member(
    service: TenancyService, org: Organization, user: User
) -> None:
    with pytest.raises(PermissionDeniedError):
        await service.get_membership_or_raise(org.id, user.id)


async def test_get_membership_or_raise_raises_for_nonexistent_org(
    service: TenancyService, user: User
) -> None:
    with pytest.raises(PermissionDeniedError):
        await service.get_membership_or_raise(uuid4(), user.id)


# --- list_orgs_for_user ------------------------------------------------------


async def test_list_orgs_for_user_returns_only_users_orgs(
    service: TenancyService,
    membership_repo: MembershipRepository,
    org_repo: OrganizationRepository,
    user: User,
) -> None:
    other_org = await org_repo.create(name="Other", slug=f"other-{uuid4().hex[:8]}")
    await membership_repo.create(user_id=user.id, org_id=other_org.id, role=MemberRole.OWNER)

    orgs = await service.list_orgs_for_user(user.id)

    assert [o.id for o in orgs] == [other_org.id]


# --- update_org ---------------------------------------------------------------


async def test_update_org_updates_name(service: TenancyService, org: Organization) -> None:
    updated = await service.update_org(org.id, name="Renamed")

    assert updated.name == "Renamed"


# --- list_members -------------------------------------------------------------


async def test_list_members_returns_org_members(
    service: TenancyService, membership_repo: MembershipRepository, org: Organization, user: User
) -> None:
    await membership_repo.create(user_id=user.id, org_id=org.id, role=MemberRole.OWNER)

    members, next_cursor = await service.list_members(org.id, None, 20)

    assert [m.user_id for m in members] == [user.id]
    assert next_cursor is None


# --- preview_invitation -------------------------------------------------------


async def test_preview_invitation_returns_invitation_and_org(
    service: TenancyService,
    invitation_repo: InvitationRepository,
    org: Organization,
    user: User,
) -> None:
    token = await _issue_invitation(invitation_repo, org, user)

    invitation, returned_org = await service.preview_invitation(token)

    assert invitation.email == "invitee@example.com"
    assert returned_org.id == org.id


async def test_preview_invitation_does_not_mark_used(
    service: TenancyService,
    invitation_repo: InvitationRepository,
    org: Organization,
    user: User,
    session: AsyncSession,
) -> None:
    token = await _issue_invitation(invitation_repo, org, user)
    invitee = User(email=f"invitee-{uuid4()}@example.com")
    session.add(invitee)
    await session.flush()

    await service.preview_invitation(token)
    membership = await service.accept_invitation(token, invitee)

    assert membership.org_id == org.id


async def test_preview_invitation_unknown_token_raises(service: TenancyService) -> None:
    with pytest.raises(AuthenticationError):
        await service.preview_invitation("not-a-real-token")


async def test_preview_invitation_expired_raises(
    service: TenancyService, invitation_repo: InvitationRepository, org: Organization, user: User
) -> None:
    token = await _issue_invitation(invitation_repo, org, user, expires_at=_past())

    with pytest.raises(AuthenticationError):
        await service.preview_invitation(token)


async def test_preview_invitation_deleted_org_raises(
    service: TenancyService,
    invitation_repo: InvitationRepository,
    org: Organization,
    user: User,
    session: AsyncSession,
) -> None:
    token = await _issue_invitation(invitation_repo, org, user)
    org.deleted_at = datetime.now(UTC)
    await session.flush()

    with pytest.raises(AuthenticationError):
        await service.preview_invitation(token)
