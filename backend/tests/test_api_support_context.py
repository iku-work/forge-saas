"""
T-262: GET /api/v1/me/support-context is the mobile app's (and any non-web
surface's) equivalent of packages/hooks' useSupportContext -- the fields a
support conversation should start with, for a user who can't run a
JavaScript widget. Uses the shared client/session fixtures from conftest.py
(T-048) like the rest of the router tests.
"""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import module_loader
from app.core.modules import EnabledModules, ModuleConfig
from app.models._mixins import utcnow
from app.modules.auth.models import User
from app.modules.auth.permissions import MemberRole
from app.modules.auth.repository import UserRepository
from app.modules.auth.utils import create_access_token, hash_password
from app.modules.billing.models import Plan, Subscription
from app.modules.tenancy.models import Organization
from app.modules.tenancy.repository import MembershipRepository, OrganizationRepository
from product import config as product_config

_PASSWORD = "Correct-Horse9"
_PATH = "/api/v1/me/support-context"


@pytest.fixture(autouse=True)
def _clear_modules_cache() -> Generator[None]:
    module_loader.get_modules.cache_clear()
    yield
    module_loader.get_modules.cache_clear()


async def _make_user(
    session: AsyncSession,
    *,
    full_name: str | None = None,
    locale: str = "en",
    is_superadmin: bool = False,
    totp_enabled: bool = False,
) -> User:
    user = await UserRepository(session).create(
        email=f"user-{uuid4()}@example.com",
        hashed_password=hash_password(_PASSWORD),
        email_verified_at=datetime.now(UTC),
        full_name=full_name,
        locale=locale,
        is_superadmin=is_superadmin,
        totp_enabled=totp_enabled,
    )
    await session.commit()
    return user


async def _make_org(session: AsyncSession, user: User, *, name: str = "Acme") -> Organization:
    org = await OrganizationRepository(session).create(name=name, slug=f"acme-{uuid4()}")
    await MembershipRepository(session).create(
        user_id=user.id, org_id=org.id, role=MemberRole.OWNER
    )
    await session.commit()
    return org


async def _make_subscription(
    session: AsyncSession, *, org_id: UUID, plan_slug: str, status: str
) -> Subscription:
    plan = Plan(
        name="Pro",
        slug=plan_slug,
        stripe_price_id_monthly="price_m",
        stripe_price_id_yearly="price_y",
        price_monthly=2900,
        price_yearly=29000,
        features={"seats": 5},
    )
    session.add(plan)
    await session.flush()
    subscription = Subscription(
        org_id=org_id,
        plan_id=plan.id,
        stripe_subscription_id=f"sub_{uuid4().hex[:8]}",
        status=status,
        current_period_start=utcnow() - timedelta(days=30),
        current_period_end=utcnow() + timedelta(days=1),
    )
    session.add(subscription)
    await session.commit()
    return subscription


def _authorize(
    client: httpx.AsyncClient,
    user: User,
    org: Organization,
    *,
    impersonator_id: UUID | None = None,
) -> httpx.AsyncClient:
    token = create_access_token(user.id, org.id, (), impersonator_id=impersonator_id)
    client.headers["Authorization"] = f"Bearer {token}"
    client.headers["X-Org-ID"] = str(org.id)
    return client


async def test_returns_the_callers_own_user_and_org_context(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await _make_user(session, full_name="Jane Doe", locale="fi")
    org = await _make_org(session, user, name="Acme Inc")
    _authorize(client, user, org)

    response = await client.get(_PATH)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["user_id"] == str(user.id)
    assert data["email"] == user.email
    assert data["full_name"] == "Jane Doe"
    assert data["org_id"] == str(org.id)
    assert data["org_name"] == "Acme Inc"
    assert data["locale"] == "fi"
    assert data["signup_at"] == user.created_at.isoformat()
    assert data["is_impersonating"] is False


async def test_reports_the_orgs_plan_and_subscription_status(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await _make_user(session)
    org = await _make_org(session, user)
    await _make_subscription(session, org_id=org.id, plan_slug="pro-team", status="past_due")
    _authorize(client, user, org)

    response = await client.get(_PATH)

    data = response.json()["data"]
    assert data["plan"] == "pro-team"
    assert data["subscription_status"] == "past_due"


async def test_org_without_a_subscription_reports_null_plan(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await _make_user(session)
    org = await _make_org(session, user)
    _authorize(client, user, org)

    response = await client.get(_PATH)

    data = response.json()["data"]
    assert data["plan"] is None
    assert data["subscription_status"] is None


async def test_billing_disabled_reports_null_plan_without_querying_billing_tables(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The subscription/plan tables don't exist at all in a billing-disabled
    product (T-062) -- a real subscription row is written here first, so a
    regression that queried it anyway would show up as a *wrong* answer, not
    just a lucky null."""
    user = await _make_user(session)
    org = await _make_org(session, user)
    await _make_subscription(session, org_id=org.id, plan_slug="pro-team", status="active")
    _authorize(client, user, org)
    monkeypatch.setattr(
        product_config,
        "MODULES",
        # notifications and file_storage both declare `requires: billing`,
        # so they have to go too -- validate_modules rejects the
        # half-disabled combination at boot.
        EnabledModules(
            billing=ModuleConfig(enabled=False),
            notifications=ModuleConfig(enabled=False),
            file_storage=ModuleConfig(enabled=False),
        ),
    )

    response = await client.get(_PATH)

    data = response.json()["data"]
    assert data["plan"] is None
    assert data["subscription_status"] is None


async def test_impersonated_request_is_flagged(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """T-142: the support widget must suppress its identity call when a
    superadmin is acting as this user -- see SupportContextResponse."""
    user = await _make_user(session)
    org = await _make_org(session, user)
    superadmin = await _make_user(session, is_superadmin=True, totp_enabled=True)
    _authorize(client, user, org, impersonator_id=superadmin.id)

    response = await client.get(_PATH)

    assert response.json()["data"]["is_impersonating"] is True


async def test_rejects_an_org_the_caller_is_not_a_member_of(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """Cross-tenant safety: a user in org A must never receive org B's
    context, even holding a valid token for their own account."""
    user = await _make_user(session)
    own_org = await _make_org(session, user, name="Own Org")
    other_user = await _make_user(session)
    other_org = await _make_org(session, other_user, name="Other Org")
    _authorize(client, user, own_org)
    client.headers["X-Org-ID"] = str(other_org.id)

    response = await client.get(_PATH)

    assert response.status_code == 403
    assert "Other Org" not in response.text


async def test_requires_authentication(client: httpx.AsyncClient) -> None:
    assert "Authorization" not in client.headers

    response = await client.get(_PATH, headers={"X-Org-ID": str(uuid4())})

    assert response.status_code == 401


async def test_requires_the_org_header(client: httpx.AsyncClient, session: AsyncSession) -> None:
    user = await _make_user(session)
    org = await _make_org(session, user)
    _authorize(client, user, org)
    del client.headers["X-Org-ID"]

    response = await client.get(_PATH)

    assert response.status_code == 400
