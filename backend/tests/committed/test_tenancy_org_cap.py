"""The free-org cap: how many free-plan organizations one user may own."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import PlanLimitError
from app.modules.auth.models import User
from app.modules.auth.permissions import MemberRole
from app.modules.auth.utils import create_access_token
from app.modules.billing.models import Plan, Subscription
from app.modules.feature_flags import service as feature_flags_service
from app.modules.tenancy import service as tenancy_service
from app.modules.tenancy.models import Membership, Organization
from app.modules.tenancy.repository import OrganizationRepository
from app.modules.tenancy.service import TenancyService

# Paid orgs come from billing, so a configuration without it can't tell them apart.
pytestmark = pytest.mark.module_dependent

CAP = 2


@pytest.fixture(autouse=True)
def _cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tenancy_service, "get_free_org_cap", lambda: CAP)


@pytest.fixture
def service(session: AsyncSession) -> TenancyService:
    return TenancyService(session)


async def _user(session: AsyncSession) -> User:
    user = User(email=f"{uuid4()}@example.com")
    session.add(user)
    await session.flush()
    return user


async def _orgs(
    session: AsyncSession, user: User, count: int, role: MemberRole
) -> list[Organization]:
    orgs = [Organization(name="Acme", slug=f"acme-{uuid4().hex[:8]}") for _ in range(count)]
    session.add_all(orgs)
    await session.flush()
    session.add_all(Membership(user_id=user.id, org_id=org.id, role=role) for org in orgs)
    await session.commit()
    return orgs


async def _put_on_paid_plan(session: AsyncSession, org: Organization) -> None:
    plan = Plan(
        name="Pro",
        slug=f"pro-{uuid4().hex[:8]}",
        stripe_price_id_monthly="price_m",
        stripe_price_id_yearly="price_y",
        price_monthly=2900,
        price_yearly=29000,
        features={},
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


async def _owned(session: AsyncSession, user: User) -> int:
    return len(await OrganizationRepository(session).list_owned_by(user.id))


async def test_creating_past_the_cap_is_refused(
    service: TenancyService, session: AsyncSession, redis: Redis
) -> None:
    user = await _user(session)
    await _orgs(session, user, CAP - 1, MemberRole.OWNER)

    await service.create_org_for_user(user, "Last free one", redis)
    with pytest.raises(PlanLimitError) as refused:
        await service.create_org_for_user(user, "One too many", redis)

    assert refused.value.message_key == "errors.free_org_limit_reached"
    assert refused.value.params == {"limit": CAP}
    assert await _owned(session, user) == CAP


async def test_paid_orgs_do_not_count(
    service: TenancyService, session: AsyncSession, redis: Redis
) -> None:
    user = await _user(session)
    first, _ = await _orgs(session, user, CAP, MemberRole.OWNER)
    await _put_on_paid_plan(session, first)

    await service.create_org_for_user(user, "Fits again", redis)

    assert await _owned(session, user) == CAP + 1


async def test_orgs_you_only_belong_to_do_not_count(
    service: TenancyService, session: AsyncSession, redis: Redis
) -> None:
    user = await _user(session)
    await _orgs(session, user, CAP, MemberRole.ADMIN)

    await service.create_org_for_user(user, "Mine", redis)

    assert await _owned(session, user) == 1


async def test_no_cap_means_no_limit(
    service: TenancyService,
    session: AsyncSession,
    redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tenancy_service, "get_free_org_cap", lambda: None)
    user = await _user(session)
    await _orgs(session, user, CAP, MemberRole.OWNER)

    await service.create_org_for_user(user, "Beyond", redis)

    assert await _owned(session, user) == CAP + 1


async def test_with_billing_off_every_org_counts(
    service: TenancyService,
    session: AsyncSession,
    redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(feature_flags_service, "is_module_enabled", lambda _module: False)
    user = await _user(session)
    first, _ = await _orgs(session, user, CAP, MemberRole.OWNER)
    await _put_on_paid_plan(session, first)

    with pytest.raises(PlanLimitError):
        await service.create_org_for_user(user, "Refused", redis)


async def test_two_requests_cannot_both_take_the_last_slot(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    redis: Redis,
) -> None:
    user = await _user(session)
    await _orgs(session, user, CAP - 1, MemberRole.OWNER)

    async def create(name: str) -> Organization:
        async with session_factory() as racing_session:
            org = await TenancyService(racing_session).create_org_for_user(user, name, redis)
            await racing_session.commit()
            return org

    results = await asyncio.gather(create("First"), create("Second"), return_exceptions=True)

    assert sorted(type(result).__name__ for result in results) == ["Organization", "PlanLimitError"]
    assert await _owned(session, user) == CAP


async def test_the_route_answers_402(client: httpx.AsyncClient, session: AsyncSession) -> None:
    user = await _user(session)
    await _orgs(session, user, CAP, MemberRole.OWNER)
    user_id = user.id

    response = await client.post(
        "/api/v1/orgs",
        json={"name": "One too many"},
        headers={"Authorization": f"Bearer {create_access_token(user_id, None, ())}"},
    )

    assert response.status_code == 402, response.text
    assert response.json()["error"]["message_key"] == "errors.free_org_limit_reached"
