"""
Verifies T-031's billing router: checkout-session and portal-session both
require BILLING_MANAGE, subscription requires BILLING_READ, plans is public,
and every gated route resolves its org from the X-Org-ID header (app.core.
dependencies' convention) rather than a path parameter, since none of these
routes have an org_id segment.

stripe.checkout.Session.create_async / stripe.billing_portal.Session.create_async /
stripe.Customer.create_async are monkeypatched (T-206 switched these call
sites onto stripe's `_async` method variants, see app.modules.billing.service)
-- this file verifies routing, auth/permission gating, and envelope shape,
not Stripe's request/response contract (see test_billing_checkout.py for
that). Same real-Postgres + real-Redis testcontainers /
httpx.AsyncClient(ASGITransport(...)) pattern as test_tenancy_router.py --
see that file's docstring for why TestClient is avoided.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
import stripe
from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from app.modules.auth.models import User
from app.modules.auth.permissions import MemberRole
from app.modules.auth.utils import create_access_token
from app.modules.billing.models import Plan, Subscription
from app.modules.billing.usage_models import UsageRecord
from app.modules.tenancy.models import Membership, Organization

# T-291: this file needs billing enabled -- its routes, its tables, or
# both. CI's module-matrix job deselects it in the combination that turns
# every optional module off.
pytestmark = pytest.mark.module_dependent


@pytest.fixture
async def owner(session: AsyncSession) -> User:
    u = User(email=f"owner-{uuid4()}@example.com")
    session.add(u)
    await session.flush()
    await session.commit()
    return u


@pytest.fixture
async def org(session: AsyncSession, owner: User) -> Organization:
    organization = Organization(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    session.add(organization)
    await session.flush()
    session.add(Membership(user_id=owner.id, org_id=organization.id, role=MemberRole.OWNER))
    await session.commit()
    return organization


async def _add_member(
    session: AsyncSession, org: Organization, *, role: MemberRole = MemberRole.MEMBER
) -> User:
    user = User(email=f"member-{uuid4()}@example.com")
    session.add(user)
    await session.flush()
    session.add(Membership(user_id=user.id, org_id=org.id, role=role))
    await session.commit()
    return user


async def _make_plan(session: AsyncSession, *, is_active: bool = True) -> Plan:
    plan = Plan(
        name="Pro",
        slug=f"pro-{uuid4().hex[:8]}",
        stripe_price_id_monthly="price_monthly_1",
        stripe_price_id_yearly="price_yearly_1",
        price_monthly=2900,
        price_yearly=29000,
        features={"seats": 5},
        is_active=is_active,
    )
    session.add(plan)
    await session.flush()
    await session.commit()
    return plan


async def _make_subscription(
    session: AsyncSession, *, org: Organization, plan: Plan
) -> Subscription:
    now = datetime.now(UTC)
    subscription = Subscription(
        org_id=org.id,
        plan_id=plan.id,
        stripe_subscription_id=f"sub_{uuid4().hex[:8]}",
        status="active",
        current_period_start=now - timedelta(days=1),
        current_period_end=now + timedelta(days=29),
    )
    session.add(subscription)
    await session.flush()
    await session.commit()
    return subscription


def _headers(user: User, org: Organization) -> dict[str, str]:
    token = create_access_token(user.id, None, ())
    return {"Authorization": f"Bearer {token}", "X-Org-ID": str(org.id)}


def _stale_headers(user: User, org: Organization) -> dict[str, str]:
    """A session that is perfectly valid but was not established recently.

    This is the ordinary state of a tab left open: the access token is fresh
    (silently refreshed), while auth_time still points at the last *real*
    sign-in, because a refresh deliberately does not advance it. See
    Session.auth_time.
    """
    token = create_access_token(user.id, None, (), auth_time=datetime.now(UTC) - timedelta(hours=1))
    return {"Authorization": f"Bearer {token}", "X-Org-ID": str(org.id)}


class _FakeCustomer:
    def __init__(self, customer_id: str) -> None:
        self.id = customer_id


class _FakeCheckoutSession:
    def __init__(self, url: str) -> None:
        self.url = url


class _FakePortalSession:
    def __init__(self, url: str) -> None:
        self.url = url


# --- POST /billing/checkout-session ------------------------------------------------


async def test_checkout_session_requires_auth(client: httpx.AsyncClient, org: Organization) -> None:
    response = await client.post(
        "/api/v1/billing/checkout-session",
        json={"plan_slug": "pro", "interval": "monthly"},
        headers={"X-Org-ID": str(org.id)},
    )

    assert response.status_code == 401


async def test_checkout_session_requires_org_header(client: httpx.AsyncClient, owner: User) -> None:
    token = create_access_token(owner.id, None, ())
    response = await client.post(
        "/api/v1/billing/checkout-session",
        json={"plan_slug": "pro", "interval": "monthly"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400


async def test_checkout_session_member_without_billing_manage_returns_403(
    client: httpx.AsyncClient, session: AsyncSession, org: Organization
) -> None:
    member = await _add_member(session, org)

    response = await client.post(
        "/api/v1/billing/checkout-session",
        json={"plan_slug": "pro", "interval": "monthly"},
        headers=_headers(member, org),
    )

    assert response.status_code == 403


async def test_checkout_session_owner_happy_path(
    client: httpx.AsyncClient,
    session: AsyncSession,
    owner: User,
    org: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = await _make_plan(session)

    async def _customer_create(**_kwargs: object) -> _FakeCustomer:
        return _FakeCustomer("cus_1")

    async def _checkout_create(**_kwargs: object) -> _FakeCheckoutSession:
        return _FakeCheckoutSession(url="https://checkout.stripe.com/test")

    monkeypatch.setattr(stripe.Customer, "create_async", _customer_create)
    monkeypatch.setattr(stripe.checkout.Session, "create_async", _checkout_create)

    response = await client.post(
        "/api/v1/billing/checkout-session",
        json={"plan_slug": plan.slug, "interval": "monthly"},
        headers=_headers(owner, org),
    )

    assert response.status_code == 200
    assert response.json()["data"]["url"] == "https://checkout.stripe.com/test"


async def test_checkout_session_does_not_require_a_recent_sign_in(
    client: httpx.AsyncClient,
    session: AsyncSession,
    owner: User,
    org: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An hour-old sign-in can still start an upgrade.

    This route carried require_recent_auth(5 minutes) until 2026-09-10, which
    meant any tab open longer than that answered 403 step_up_auth_required to
    "Upgrade" -- a checkout interruption bought with approximately no
    security, since a stolen session reaching here only yields a Stripe URL
    on which the attacker would have to enter their own card. The control now
    sits on /portal-session, which is asserted below.
    """
    plan = await _make_plan(session)

    async def _customer_create(**_kwargs: object) -> _FakeCustomer:
        return _FakeCustomer("cus_1")

    async def _checkout_create(**_kwargs: object) -> _FakeCheckoutSession:
        return _FakeCheckoutSession(url="https://checkout.stripe.com/test")

    monkeypatch.setattr(stripe.Customer, "create_async", _customer_create)
    monkeypatch.setattr(stripe.checkout.Session, "create_async", _checkout_create)

    response = await client.post(
        "/api/v1/billing/checkout-session",
        json={"plan_slug": plan.slug, "interval": "monthly"},
        headers=_stale_headers(owner, org),
    )

    assert response.status_code == 200


async def test_checkout_session_unknown_plan_returns_404(
    client: httpx.AsyncClient,
    owner: User,
    org: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _customer_create(**_kwargs: object) -> _FakeCustomer:
        return _FakeCustomer("cus_1")

    monkeypatch.setattr(stripe.Customer, "create_async", _customer_create)

    response = await client.post(
        "/api/v1/billing/checkout-session",
        json={"plan_slug": "does-not-exist", "interval": "monthly"},
        headers=_headers(owner, org),
    )

    assert response.status_code == 404


# --- POST /billing/portal-session --------------------------------------------------


async def test_portal_session_requires_auth(client: httpx.AsyncClient, org: Organization) -> None:
    response = await client.post(
        "/api/v1/billing/portal-session", headers={"X-Org-ID": str(org.id)}
    )

    assert response.status_code == 401


async def test_portal_session_member_without_billing_manage_returns_403(
    client: httpx.AsyncClient, session: AsyncSession, org: Organization
) -> None:
    member = await _add_member(session, org)

    response = await client.post("/api/v1/billing/portal-session", headers=_headers(member, org))

    assert response.status_code == 403


async def test_portal_session_requires_a_recent_sign_in(
    client: httpx.AsyncClient,
    owner: User,
    org: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Stripe Customer Portal is the billing surface worth protecting.

    Behind it sit the payment instrument on file, the invoice history, and --
    where the portal is configured for it -- cancelling outright. Each is
    damage a hijacked session can do by itself, which is what makes an
    interruption worth its cost here and not on checkout.
    """

    async def _unreached(**_kwargs: object) -> object:
        raise AssertionError("Stripe must not be called when step-up fails")

    monkeypatch.setattr(stripe.Customer, "create_async", _unreached)
    monkeypatch.setattr(stripe.billing_portal.Session, "create_async", _unreached)

    response = await client.post(
        "/api/v1/billing/portal-session", headers=_stale_headers(owner, org)
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "step_up_auth_required"


async def test_portal_session_owner_happy_path(
    client: httpx.AsyncClient,
    owner: User,
    org: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _customer_create(**_kwargs: object) -> _FakeCustomer:
        return _FakeCustomer("cus_1")

    async def _portal_create(**_kwargs: object) -> _FakePortalSession:
        return _FakePortalSession(url="https://billing.stripe.com/portal/test")

    monkeypatch.setattr(stripe.Customer, "create_async", _customer_create)
    monkeypatch.setattr(stripe.billing_portal.Session, "create_async", _portal_create)

    response = await client.post("/api/v1/billing/portal-session", headers=_headers(owner, org))

    assert response.status_code == 200
    assert response.json()["data"]["url"] == "https://billing.stripe.com/portal/test"


# --- GET /billing/subscription ------------------------------------------------------


async def test_get_subscription_requires_auth(client: httpx.AsyncClient, org: Organization) -> None:
    response = await client.get("/api/v1/billing/subscription", headers={"X-Org-ID": str(org.id)})

    assert response.status_code == 401


async def test_get_subscription_member_without_billing_read_returns_403(
    client: httpx.AsyncClient, session: AsyncSession, org: Organization
) -> None:
    member = await _add_member(session, org)

    response = await client.get("/api/v1/billing/subscription", headers=_headers(member, org))

    assert response.status_code == 403


async def test_get_subscription_admin_can_read(
    client: httpx.AsyncClient, session: AsyncSession, org: Organization
) -> None:
    admin = await _add_member(session, org, role=MemberRole.ADMIN)

    response = await client.get("/api/v1/billing/subscription", headers=_headers(admin, org))

    assert response.status_code == 200
    assert response.json()["data"] is None


# --- GET /billing/usage -------------------------------------------------------------


async def test_get_usage_requires_auth(client: httpx.AsyncClient, org: Organization) -> None:
    response = await client.get("/api/v1/billing/usage", headers={"X-Org-ID": str(org.id)})

    assert response.status_code == 401


async def test_get_usage_member_without_billing_read_returns_403(
    client: httpx.AsyncClient, session: AsyncSession, org: Organization
) -> None:
    member = await _add_member(session, org)

    response = await client.get("/api/v1/billing/usage", headers=_headers(member, org))

    assert response.status_code == 403


async def test_get_usage_admin_with_no_subscription_returns_empty_list(
    client: httpx.AsyncClient, session: AsyncSession, org: Organization
) -> None:
    admin = await _add_member(session, org, role=MemberRole.ADMIN)

    response = await client.get("/api/v1/billing/usage", headers=_headers(admin, org))

    assert response.status_code == 200
    assert response.json()["data"] == []


async def test_get_usage_admin_reads_estimated_cost(
    client: httpx.AsyncClient, session: AsyncSession, org: Organization
) -> None:
    admin = await _add_member(session, org, role=MemberRole.ADMIN)
    plan = Plan(
        name="Pro",
        slug=f"pro-{uuid4().hex[:8]}",
        stripe_price_id_monthly="price_monthly_usage",
        stripe_price_id_yearly="price_yearly_usage",
        price_monthly=2900,
        price_yearly=29000,
        features={"seats": 5},
        usage_meter_event_names={"api_calls": "api_calls_meter"},
        usage_prices_micros={"api_calls": 100},  # $0.0001/call
    )
    session.add(plan)
    await session.flush()
    await session.commit()
    subscription = await _make_subscription(session, org=org, plan=plan)
    session.add(
        UsageRecord(
            org_id=org.id,
            metric="api_calls",
            quantity=42,
            occurred_at=subscription.current_period_start + timedelta(hours=1),
        )
    )
    await session.commit()

    response = await client.get("/api/v1/billing/usage", headers=_headers(admin, org))

    assert response.status_code == 200
    assert response.json()["data"] == [
        {
            "metric": "api_calls",
            "quantity": 42,
            "unit_price_micros": 100,
            "estimated_cost_micros": 4_200,
        }
    ]


# --- GET /billing/plans -------------------------------------------------------------


async def test_list_plans_is_public(client: httpx.AsyncClient, session: AsyncSession) -> None:
    active = await _make_plan(session)
    inactive = await _make_plan(session, is_active=False)

    response = await client.get("/api/v1/billing/plans")

    assert response.status_code == 200
    slugs = {p["slug"] for p in response.json()["data"]}
    assert active.slug in slugs
    assert inactive.slug not in slugs
