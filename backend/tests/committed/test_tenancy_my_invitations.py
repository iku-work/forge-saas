"""Invitations for people who already have an account: they hear about it in
the app, can accept or quietly decline there, and the inviter hears back.

Accepting without the emailed link needs a verified address, since that is
then the only proof the person owns the address the invitation went to.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.models._mixins import utcnow
from app.modules.auth.models import User
from app.modules.auth.permissions import MemberRole
from app.modules.auth.utils import create_access_token
from app.modules.notifications.models import Notification
from app.modules.tenancy import service as tenancy_service
from app.modules.tenancy.models import Invitation, Membership, Organization
from app.modules.tenancy.service import TenancyService

# The notifications these flows send need the notifications module.
pytestmark = pytest.mark.module_dependent


@pytest.fixture
def service(session: AsyncSession) -> TenancyService:
    return TenancyService(session)


async def _user(session: AsyncSession, *, verified: bool = True) -> User:
    user = User(
        email=f"{uuid4()}@example.com",
        email_verified_at=datetime.now(UTC) if verified else None,
    )
    session.add(user)
    await session.flush()
    return user


async def _org(session: AsyncSession, owner: User, name: str = "Acme") -> Organization:
    org = Organization(name=name, slug=f"{name.lower()}-{uuid4().hex[:8]}")
    session.add(org)
    await session.flush()
    session.add(Membership(user_id=owner.id, org_id=org.id, role=MemberRole.OWNER))
    await session.flush()
    return org


async def _owner_membership(session: AsyncSession, user: User, org: Organization) -> Membership:
    m = cast("Any", Membership)
    stmt = select(Membership).where(m.user_id == user.id, m.org_id == org.id)
    return (await session.execute(stmt)).scalar_one()


async def _notifications(session: AsyncSession, user: User) -> list[Notification]:
    m = cast("Any", Notification)
    stmt = select(Notification).where(m.user_id == user.id).order_by(m.created_at)
    return list((await session.execute(stmt)).scalars().all())


async def _invite(  # noqa: PLR0913 -- the whole invite context, so each test reads as one line
    service: TenancyService,
    session: AsyncSession,
    redis: Redis,
    org: Organization,
    inviter: User,
    email: str,
) -> Invitation:
    membership = await _owner_membership(session, inviter, org)
    await service.invite(org.id, membership, email, MemberRole.MEMBER, redis)
    m = cast("Any", Invitation)
    stmt = select(Invitation).where(m.org_id == org.id, m.email == email)
    return (await session.execute(stmt)).scalar_one()


# --- inviting an existing user ---------------------------------------------------


async def test_an_existing_user_is_told_in_the_app(
    service: TenancyService, session: AsyncSession, redis_client: Redis
) -> None:
    inviter = await _user(session)
    invitee = await _user(session)
    org = await _org(session, inviter)

    await _invite(service, session, redis_client, org, inviter, invitee.email)

    [received] = await _notifications(session, invitee)
    assert received.type == "invitation_received"
    assert received.org_id is None
    assert received.params == {"org_name": "Acme"}
    assert received.action_url == "/invitations"
    assert [n.type for n in await _notifications(session, inviter)] == ["member_invited"]


async def test_an_address_with_no_account_gets_only_the_email(
    service: TenancyService, session: AsyncSession, redis_client: Redis
) -> None:
    inviter = await _user(session)
    org = await _org(session, inviter)

    await _invite(service, session, redis_client, org, inviter, f"{uuid4()}@example.com")

    m = cast("Any", Notification)
    types = (await session.execute(select(m.type).where(m.type == "invitation_received"))).all()
    assert types == []


async def test_inviting_works_with_the_notifications_module_off(
    service: TenancyService,
    session: AsyncSession,
    redis_client: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the module off a product has no notification table, so inviting
    must not try to write one."""
    monkeypatch.setattr(tenancy_service, "is_enabled", lambda module: module != "notifications")
    inviter = await _user(session)
    invitee = await _user(session)
    org = await _org(session, inviter)

    invitation = await _invite(service, session, redis_client, org, inviter, invitee.email)
    await service.accept_my_invitation(invitation.id, invitee, redis_client)

    assert await _notifications(session, invitee) == []
    assert await _notifications(session, inviter) == []


# --- the invitee's list ----------------------------------------------------------


async def test_the_list_shows_open_invitations_to_this_address(
    service: TenancyService, session: AsyncSession, redis_client: Redis
) -> None:
    inviter = await _user(session)
    invitee = await _user(session)
    open_org = await _org(session, inviter, "Open")
    expired_org = await _org(session, inviter, "Expired")
    deleted_org = await _org(session, inviter, "Deleted")
    joined_org = await _org(session, inviter, "Joined")
    # Typed differently by the inviter: the address still matches.
    await _invite(service, session, redis_client, open_org, inviter, invitee.email.upper())
    expired = await _invite(service, session, redis_client, expired_org, inviter, invitee.email)
    expired.expires_at = utcnow() - timedelta(minutes=1)
    await _invite(service, session, redis_client, deleted_org, inviter, invitee.email)
    deleted_org.deleted_at = utcnow()
    await _invite(service, session, redis_client, joined_org, inviter, invitee.email)
    session.add(Membership(user_id=invitee.id, org_id=joined_org.id, role=MemberRole.MEMBER))
    await session.flush()

    rows = await service.list_my_invitations(invitee)

    assert [org.name for _, org in rows] == ["Open"]


async def test_an_unverified_address_sees_no_list(
    service: TenancyService, session: AsyncSession, redis_client: Redis
) -> None:
    inviter = await _user(session)
    invitee = await _user(session, verified=False)
    org = await _org(session, inviter)
    await _invite(service, session, redis_client, org, inviter, invitee.email)

    assert await service.list_my_invitations(invitee) == []


# --- accepting and declining in the app -----------------------------------------


async def test_accepting_joins_and_tells_the_inviter(
    service: TenancyService, session: AsyncSession, redis_client: Redis
) -> None:
    inviter = await _user(session)
    invitee = await _user(session)
    org = await _org(session, inviter)
    invitation = await _invite(service, session, redis_client, org, inviter, invitee.email)

    membership = await service.accept_my_invitation(invitation.id, invitee, redis_client)

    assert membership.org_id == org.id
    assert membership.role == MemberRole.MEMBER
    accepted = (await _notifications(session, inviter))[-1]
    assert accepted.type == "invitation_accepted"
    assert accepted.org_id == org.id
    assert accepted.params == {"email": invitee.email, "org_name": "Acme"}
    assert await service.list_my_invitations(invitee) == []


async def test_an_inviter_who_left_is_not_told(
    service: TenancyService, session: AsyncSession, redis_client: Redis
) -> None:
    inviter = await _user(session)
    other_owner = await _user(session)
    invitee = await _user(session)
    org = await _org(session, other_owner)
    session.add(Membership(user_id=inviter.id, org_id=org.id, role=MemberRole.ADMIN))
    await session.flush()
    invitation = await _invite(service, session, redis_client, org, inviter, invitee.email)
    m = cast("Any", Membership)
    await session.execute(delete(Membership).where(m.user_id == inviter.id))

    await service.accept_my_invitation(invitation.id, invitee, redis_client)

    assert [n.type for n in await _notifications(session, inviter)] == ["member_invited"]


async def test_accepting_by_link_tells_the_inviter_too(
    service: TenancyService, session: AsyncSession, redis_client: Redis
) -> None:
    inviter = await _user(session)
    invitee = await _user(session)
    org = await _org(session, inviter)
    membership = await _owner_membership(session, inviter, org)
    captured: dict[str, str] = {}
    real_sign = tenancy_service.sign_url_token

    def _capture(payload: dict[str, str], max_age: int) -> str:
        captured["token"] = real_sign(payload, max_age)
        return captured["token"]

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(tenancy_service, "sign_url_token", _capture)
        await service.invite(org.id, membership, invitee.email, MemberRole.MEMBER, redis_client)

    await service.accept_invitation(captured["token"], invitee, redis_client)

    assert (await _notifications(session, inviter))[-1].type == "invitation_accepted"


async def test_declining_withdraws_it_quietly(
    service: TenancyService, session: AsyncSession, redis_client: Redis
) -> None:
    inviter = await _user(session)
    invitee = await _user(session)
    org = await _org(session, inviter)
    invitation = await _invite(service, session, redis_client, org, inviter, invitee.email)
    invitation_id = invitation.id

    await service.decline_my_invitation(invitation_id, invitee)

    assert await session.get(Invitation, invitation_id) is None
    assert [n.type for n in await _notifications(session, inviter)] == ["member_invited"]
    with pytest.raises(NotFoundError):
        await service.accept_my_invitation(invitation_id, invitee, redis_client)


async def test_an_unverified_address_cannot_accept_by_id(
    service: TenancyService, session: AsyncSession, redis_client: Redis
) -> None:
    inviter = await _user(session)
    invitee = await _user(session, verified=False)
    org = await _org(session, inviter)
    invitation = await _invite(service, session, redis_client, org, inviter, invitee.email)

    with pytest.raises(PermissionDeniedError) as raised:
        await service.accept_my_invitation(invitation.id, invitee, redis_client)
    assert raised.value.message_key == "errors.invitation_needs_verified_email"
    with pytest.raises(PermissionDeniedError):
        await service.decline_my_invitation(invitation.id, invitee)


async def test_someone_elses_invitation_is_not_found(
    service: TenancyService, session: AsyncSession, redis_client: Redis
) -> None:
    inviter = await _user(session)
    invitee = await _user(session)
    bystander = await _user(session)
    org = await _org(session, inviter)
    invitation = await _invite(service, session, redis_client, org, inviter, invitee.email)

    with pytest.raises(NotFoundError):
        await service.accept_my_invitation(invitation.id, bystander, redis_client)
    with pytest.raises(NotFoundError):
        await service.decline_my_invitation(invitation.id, bystander)


async def test_an_expired_invitation_cannot_be_accepted_by_id(
    service: TenancyService, session: AsyncSession, redis_client: Redis
) -> None:
    inviter = await _user(session)
    invitee = await _user(session)
    org = await _org(session, inviter)
    invitation = await _invite(service, session, redis_client, org, inviter, invitee.email)
    invitation.expires_at = utcnow() - timedelta(minutes=1)
    await session.flush()

    with pytest.raises(NotFoundError):
        await service.accept_my_invitation(invitation.id, invitee, redis_client)


# --- HTTP ---------------------------------------------------------------------


async def test_the_routes_list_accept_and_decline(
    client: httpx.AsyncClient, session: AsyncSession, redis_client: Redis
) -> None:
    service = TenancyService(session)
    inviter = await _user(session)
    invitee = await _user(session)
    first = await _org(session, inviter, "First")
    second = await _org(session, inviter, "Second")
    to_accept = await _invite(service, session, redis_client, first, inviter, invitee.email)
    to_decline = await _invite(service, session, redis_client, second, inviter, invitee.email)
    accept_id, decline_id, first_id = to_accept.id, to_decline.id, first.id
    await session.commit()
    auth = {"Authorization": f"Bearer {create_access_token(invitee.id, None, ())}"}

    listed = await client.get("/api/v1/me/invitations", headers=auth)
    accepted = await client.post(f"/api/v1/me/invitations/{accept_id}/accept", headers=auth)
    declined = await client.post(f"/api/v1/me/invitations/{decline_id}/decline", headers=auth)
    after = await client.get("/api/v1/me/invitations", headers=auth)

    assert listed.status_code == 200
    assert {row["org_name"] for row in listed.json()["data"]} == {"First", "Second"}
    assert accepted.status_code == 200
    assert accepted.json()["data"]["org_id"] == str(first_id)
    assert declined.status_code == 200
    assert after.json()["data"] == []
