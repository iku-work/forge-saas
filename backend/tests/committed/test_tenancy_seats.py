"""Review M2: a plan's `seats` limit, at invite, at accept, and when two people race."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import PlanLimitError
from app.modules.auth.models import User
from app.modules.auth.permissions import MemberRole
from app.modules.auth.utils import create_access_token, hash_token, sign_url_token
from app.modules.billing.models import Plan, Subscription
from app.modules.feature_flags import service as feature_flags_service
from app.modules.tenancy.models import Invitation, Membership, Organization
from app.modules.tenancy.repository import InvitationRepository, MembershipRepository
from app.modules.tenancy.service import _INVITATION_TTL_SECONDS, TenancyService

# The limit comes from billing, so a configuration without it has no limit to test.
pytestmark = pytest.mark.module_dependent

FREE_PLAN_SEATS = 3


@pytest.fixture
def service(session: AsyncSession) -> TenancyService:
    return TenancyService(session)


async def _user(session: AsyncSession) -> User:
    user = User(email=f"{uuid4()}@example.com")
    session.add(user)
    await session.flush()
    return user


async def _org_with_members(
    session: AsyncSession, count: int
) -> tuple[Organization, list[Membership]]:
    org = Organization(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    session.add(org)
    await session.flush()
    memberships = []
    for index in range(count):
        user = await _user(session)
        role = MemberRole.OWNER if index == 0 else MemberRole.MEMBER
        memberships.append(Membership(user_id=user.id, org_id=org.id, role=role))
    session.add_all(memberships)
    await session.commit()
    return org, memberships


async def _put_on_plan(session: AsyncSession, org: Organization, features: dict[str, int]) -> None:
    plan = Plan(
        name="Pro",
        slug=f"pro-{uuid4().hex[:8]}",
        stripe_price_id_monthly="price_m",
        stripe_price_id_yearly="price_y",
        price_monthly=2900,
        price_yearly=29000,
        features=features,
    )
    session.add(plan)
    await session.flush()
    now = datetime.now(UTC)
    session.add(
        Subscription(
            org_id=org.id,
            plan_id=plan.id,
            stripe_subscription_id=f"sub_{uuid4().hex[:8]}",
            status="active",
            current_period_start=now - timedelta(days=1),
            current_period_end=now + timedelta(days=29),
        )
    )
    await session.commit()


async def _invitation_for(
    session: AsyncSession,
    org: Organization,
    inviter: Membership,
    email: str,
    expires_in: timedelta = timedelta(hours=72),
) -> str:
    invitation_id = uuid4()
    token = sign_url_token({"invitation_id": str(invitation_id)}, _INVITATION_TTL_SECONDS)
    await InvitationRepository(session).create(
        invitation_id=invitation_id,
        org_id=org.id,
        email=email,
        role=MemberRole.MEMBER,
        token_hash=hash_token(token),
        invited_by=inviter.user_id,
        expires_at=datetime.now(UTC) + expires_in,
    )
    await session.commit()
    return token


async def _invite(
    service: TenancyService, org: Organization, inviter: Membership, redis: Redis
) -> None:
    await service.invite(org.id, inviter, f"{uuid4()}@example.com", MemberRole.MEMBER, redis)


async def _invitation_count(session: AsyncSession, org: Organization) -> int:
    invitation = cast("Any", Invitation)
    statement = select(func.count()).select_from(Invitation).where(invitation.org_id == org.id)
    return (await session.execute(statement)).scalar_one()


async def test_pending_invitations_count_when_inviting(
    service: TenancyService, session: AsyncSession, redis: Redis
) -> None:
    org, (owner, *_) = await _org_with_members(session, 1)
    for _ in range(FREE_PLAN_SEATS - 1):
        await _invite(service, org, owner, redis)

    with pytest.raises(PlanLimitError) as refused:
        await _invite(service, org, owner, redis)

    assert refused.value.message_key == "errors.seat_limit_reached"
    assert await _invitation_count(session, org) == FREE_PLAN_SEATS - 1


async def test_an_expired_invitation_does_not_hold_a_seat(
    service: TenancyService, session: AsyncSession, redis: Redis
) -> None:
    org, (owner, *_) = await _org_with_members(session, FREE_PLAN_SEATS - 1)
    await _invitation_for(session, org, owner, "gone@example.com", timedelta(hours=-1))

    await _invite(service, org, owner, redis)

    assert await _invitation_count(session, org) == 2


async def test_a_full_org_refuses_an_accept_and_the_link_survives_it(
    service: TenancyService, session: AsyncSession, redis: Redis
) -> None:
    org, (owner, member, *_) = await _org_with_members(session, FREE_PLAN_SEATS)
    invitee = await _user(session)
    token = await _invitation_for(session, org, owner, invitee.email)

    with pytest.raises(PlanLimitError) as refused:
        await service.accept_invitation(token, invitee, redis)
    assert refused.value.message_key == "errors.seat_limit_reached_to_join"

    await MembershipRepository(session).remove(member.id)
    joined = await service.accept_invitation(token, invitee, redis)

    assert joined.user_id == invitee.id


async def test_a_pending_invitation_can_take_the_last_seat(
    service: TenancyService, session: AsyncSession, redis: Redis
) -> None:
    org, (owner, *_) = await _org_with_members(session, FREE_PLAN_SEATS - 1)
    invitee = await _user(session)
    token = await _invitation_for(session, org, owner, invitee.email)

    joined = await service.accept_invitation(token, invitee, redis)

    assert joined.org_id == org.id


async def test_an_org_over_its_limit_keeps_its_members_and_cannot_add_more(
    service: TenancyService, session: AsyncSession, redis: Redis
) -> None:
    over_the_limit = FREE_PLAN_SEATS + 2
    org, (owner, *_) = await _org_with_members(session, over_the_limit)

    with pytest.raises(PlanLimitError):
        await _invite(service, org, owner, redis)

    assert await MembershipRepository(session).count_by_org(org.id) == over_the_limit


async def test_the_limit_comes_from_the_orgs_plan(
    service: TenancyService, session: AsyncSession, redis: Redis
) -> None:
    org, (owner, *_) = await _org_with_members(session, FREE_PLAN_SEATS)
    await _put_on_plan(session, org, {"seats": FREE_PLAN_SEATS + 1})

    await _invite(service, org, owner, redis)
    with pytest.raises(PlanLimitError):
        await _invite(service, org, owner, redis)


async def test_a_plan_with_no_seats_number_is_unlimited(
    service: TenancyService, session: AsyncSession, redis: Redis
) -> None:
    org, (owner, *_) = await _org_with_members(session, FREE_PLAN_SEATS)
    await _put_on_plan(session, org, {"storage_gb": 1})

    await _invite(service, org, owner, redis)

    assert await _invitation_count(session, org) == 1


async def test_billing_off_means_no_limit(
    service: TenancyService,
    session: AsyncSession,
    redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(feature_flags_service, "is_module_enabled", lambda _module: False)
    org, (owner, *_) = await _org_with_members(session, FREE_PLAN_SEATS)

    await _invite(service, org, owner, redis)

    assert await _invitation_count(session, org) == 1


async def test_two_people_cannot_both_take_the_last_seat(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    redis: Redis,
) -> None:
    org, (owner, *_) = await _org_with_members(session, FREE_PLAN_SEATS - 1)
    first, second = await _user(session), await _user(session)
    first_token = await _invitation_for(session, org, owner, first.email)
    second_token = await _invitation_for(session, org, owner, second.email)

    async def accept(token: str, user: User) -> Membership:
        async with session_factory() as racing_session:
            membership = await TenancyService(racing_session).accept_invitation(token, user, redis)
            await racing_session.commit()
            return membership

    results = await asyncio.gather(
        accept(first_token, first), accept(second_token, second), return_exceptions=True
    )

    assert sorted(type(result).__name__ for result in results) == ["Membership", "PlanLimitError"]
    assert await MembershipRepository(session).count_by_org(org.id) == FREE_PLAN_SEATS


async def test_the_routes_answer_402(client: httpx.AsyncClient, session: AsyncSession) -> None:
    org, (owner, *_) = await _org_with_members(session, FREE_PLAN_SEATS)
    owner_id = owner.user_id
    invitee = await _user(session)
    token = await _invitation_for(session, org, owner, invitee.email)
    await session.commit()

    invite = await client.post(
        f"/api/v1/orgs/{org.id}/invitations",
        json={"email": "someone@example.com", "role": "member"},
        headers={"Authorization": f"Bearer {create_access_token(owner_id, None, ())}"},
    )
    accept = await client.post(
        f"/api/v1/invitations/{token}/accept",
        headers={"Authorization": f"Bearer {create_access_token(invitee.id, None, ())}"},
    )

    assert invite.status_code == 402, invite.text
    assert invite.json()["error"]["message_key"] == "errors.seat_limit_reached"
    assert accept.status_code == 402, accept.text
    assert accept.json()["error"]["message_key"] == "errors.seat_limit_reached_to_join"
