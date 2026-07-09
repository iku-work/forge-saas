"""
Verifies T-141's plan-override / trial-extension / comp-credit admin writes
under /api/v1/admin/orgs/{id}/{plan,extend-trial,comp-credit}: superadmin
gating, audit diffs, and the Stripe interactions -- stripe.Subscription.modify
/ stripe.Coupon.create are monkeypatched rather than hitting Stripe's network,
same convention as test_billing_checkout.py.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
import stripe
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.repository import AuditRepository
from app.modules.auth.repository import UserRepository
from app.modules.auth.utils import create_access_token, hash_password
from app.modules.billing.models import Plan, Subscription, WebhookEvent
from app.modules.billing.repository import SubscriptionRepository
from app.modules.billing.service import BillingService
from app.modules.tenancy.models import Organization
from app.modules.tenancy.repository import OrganizationRepository

_PASSWORD = "Correct-Horse9"


@pytest.fixture
def redis(_redis_client: Redis) -> Redis:
    return _redis_client


async def _make_plan(session: AsyncSession, *, slug: str | None = None) -> Plan:
    plan = Plan(
        name="Pro",
        slug=slug or f"pro-{uuid4().hex[:8]}",
        stripe_price_id_monthly="price_m",
        stripe_price_id_yearly="price_y",
        price_monthly=2900,
        price_yearly=29000,
        features={},
    )
    session.add(plan)
    await session.flush()
    return plan


async def _make_org(session: AsyncSession) -> Organization:
    return await OrganizationRepository(session).create(name="Acme", slug=f"acme-{uuid4().hex[:8]}")


async def _make_subscription(  # noqa: PLR0913 -- test helper covers every optional override the tests below need
    session: AsyncSession,
    org: Organization,
    plan: Plan,
    *,
    stripe_subscription_id: str | None = None,
    status: str = "active",
    trial_end: datetime | None = None,
) -> Subscription:
    subscription = Subscription(
        org_id=org.id,
        plan_id=plan.id,
        stripe_subscription_id=stripe_subscription_id or f"sub_{uuid4().hex[:8]}",
        status=status,
        current_period_start=datetime.now(UTC) - timedelta(days=10),
        current_period_end=datetime.now(UTC) + timedelta(days=20),
        trial_end=trial_end,
    )
    session.add(subscription)
    await session.flush()
    return subscription


async def _as_superadmin(client: httpx.AsyncClient, session: AsyncSession) -> httpx.AsyncClient:
    # T-218: require_superadmin now also gates on 2FA being enrolled.
    user = await UserRepository(session).create(
        email=f"admin-{uuid4()}@example.com",
        hashed_password=hash_password(_PASSWORD),
        email_verified_at=datetime.now(UTC),
        is_superadmin=True,
        totp_enabled=True,
    )
    await session.commit()
    token = create_access_token(user.id, None, ())
    client.headers["Authorization"] = f"Bearer {token}"
    return client


async def _as_non_superadmin(client: httpx.AsyncClient, session: AsyncSession) -> httpx.AsyncClient:
    user = await UserRepository(session).create(
        email=f"user-{uuid4()}@example.com",
        hashed_password=hash_password(_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    await session.commit()
    token = create_access_token(user.id, None, ())
    client.headers["Authorization"] = f"Bearer {token}"
    return client


# ---- PUT /orgs/{id}/plan ------------------------------------------------------


async def test_set_plan_denies_non_superadmin(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    org = await _make_org(session)
    await session.commit()
    admin_client = await _as_non_superadmin(client, session)

    response = await admin_client.put(
        f"/api/v1/admin/orgs/{org.id}/plan", json={"plan_slug": "pro", "free_override": True}
    )

    assert response.status_code == 403


async def test_set_plan_returns_404_for_unknown_plan(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    org = await _make_org(session)
    await session.commit()
    admin_client = await _as_superadmin(client, session)

    response = await admin_client.put(
        f"/api/v1/admin/orgs/{org.id}/plan",
        json={"plan_slug": "does-not-exist", "free_override": True},
    )

    assert response.status_code == 404


async def test_set_plan_creates_free_override_subscription_and_audit_event(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    org = await _make_org(session)
    plan = await _make_plan(session)
    await session.commit()
    admin_client = await _as_superadmin(client, session)

    response = await admin_client.put(
        f"/api/v1/admin/orgs/{org.id}/plan",
        json={
            "plan_slug": plan.slug,
            "billing_interval": "yearly",
            "free_override": True,
            "expires_at": "2027-01-01T00:00:00Z",
        },
    )

    assert response.status_code == 200
    subscription = await SubscriptionRepository(session).get_by_org_id(org.id)
    assert subscription is not None
    assert subscription.plan_id == plan.id
    assert subscription.billing_interval == "yearly"
    assert subscription.free_override is True
    assert subscription.stripe_subscription_id.startswith("admin_override_")

    events, _ = await AuditRepository(session).list_cursor(org.id)
    matching = [e for e in events if e.action == "admin.org.plan_override"]
    assert len(matching) == 1
    assert matching[0].severity == "elevated"
    diff = matching[0].diff
    assert diff is not None
    assert diff["before"] is None
    assert diff["after"]["plan_slug"] == plan.slug


async def test_set_plan_overwrites_existing_subscription_with_before_diff(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    org = await _make_org(session)
    old_plan = await _make_plan(session)
    new_plan = await _make_plan(session)
    await _make_subscription(session, org, old_plan)
    await session.commit()
    admin_client = await _as_superadmin(client, session)

    response = await admin_client.put(
        f"/api/v1/admin/orgs/{org.id}/plan",
        json={"plan_slug": new_plan.slug, "free_override": False},
    )

    assert response.status_code == 200
    subscription = await SubscriptionRepository(session).get_by_org_id(org.id)
    assert subscription is not None
    assert subscription.plan_id == new_plan.id

    events, _ = await AuditRepository(session).list_cursor(org.id)
    matching = [e for e in events if e.action == "admin.org.plan_override"]
    diff = matching[0].diff
    assert diff is not None
    assert diff["before"]["plan_slug"] == old_plan.slug


# ---- POST /orgs/{id}/extend-trial ---------------------------------------------


async def test_extend_trial_denies_non_superadmin(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    org = await _make_org(session)
    await session.commit()
    admin_client = await _as_non_superadmin(client, session)

    response = await admin_client.post(
        f"/api/v1/admin/orgs/{org.id}/extend-trial", json={"days": 7}
    )

    assert response.status_code == 403


async def test_extend_trial_returns_404_when_no_subscription(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    org = await _make_org(session)
    await session.commit()
    admin_client = await _as_superadmin(client, session)

    response = await admin_client.post(
        f"/api/v1/admin/orgs/{org.id}/extend-trial", json={"days": 7}
    )

    assert response.status_code == 404


async def test_extend_trial_updates_trial_end_and_calls_stripe(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = await _make_org(session)
    plan = await _make_plan(session)
    trial_end = datetime.now(UTC) + timedelta(days=3)
    stripe_subscription_id = f"sub_real_{uuid4().hex[:8]}"
    subscription = await _make_subscription(
        session,
        org,
        plan,
        stripe_subscription_id=stripe_subscription_id,
        trial_end=trial_end,
    )
    await session.commit()

    captured: dict[str, Any] = {}

    def fake_modify(sub_id: str, **kwargs: object) -> object:
        captured["id"] = sub_id
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(stripe.Subscription, "modify", fake_modify)
    admin_client = await _as_superadmin(client, session)

    response = await admin_client.post(
        f"/api/v1/admin/orgs/{org.id}/extend-trial", json={"days": 7}
    )

    assert response.status_code == 200
    assert captured["id"] == subscription.stripe_subscription_id
    assert captured["kwargs"]["proration_behavior"] == "none"

    refreshed = await SubscriptionRepository(session).get_by_org_id(org.id)
    assert refreshed is not None
    assert refreshed.trial_end is not None
    assert refreshed.trial_end - trial_end == timedelta(days=7)

    events, _ = await AuditRepository(session).list_cursor(org.id)
    matching = [e for e in events if e.action == "admin.org.extend_trial"]
    assert len(matching) == 1
    assert matching[0].severity == "elevated"
    diff = matching[0].diff
    assert diff is not None
    assert diff["days"] == 7


async def test_extend_trial_skips_stripe_for_free_override_subscription(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = await _make_org(session)
    plan = await _make_plan(session)
    await _make_subscription(
        session,
        org,
        plan,
        stripe_subscription_id=f"admin_override_{uuid4()}",
        trial_end=datetime.now(UTC) + timedelta(days=3),
    )
    await session.commit()

    def fail_if_called(*_args: object, **_kwargs: object) -> object:
        msg = "stripe.Subscription.modify must not be called for a free_override sub"
        raise AssertionError(msg)

    monkeypatch.setattr(stripe.Subscription, "modify", fail_if_called)
    admin_client = await _as_superadmin(client, session)

    response = await admin_client.post(
        f"/api/v1/admin/orgs/{org.id}/extend-trial", json={"days": 7}
    )

    assert response.status_code == 200


async def test_extend_trial_reconciles_with_later_webhook(
    client: httpx.AsyncClient, session: AsyncSession, redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    # T-141's own requirement: "webhook reconciliation respects manual
    # extensions" -- after a real extend-trial call, Stripe's subscription
    # object itself now has the new trial_end (we called Subscription.modify
    # for real above), so a later customer.subscription.updated webhook that
    # echoes that same trial_end back must not revert the local extension.
    org = await _make_org(session)
    plan = await _make_plan(session)
    trial_end = datetime.now(UTC) + timedelta(days=3)
    stripe_subscription_id = f"sub_real_{uuid4().hex[:8]}"
    await _make_subscription(
        session, org, plan, stripe_subscription_id=stripe_subscription_id, trial_end=trial_end
    )
    await session.commit()

    monkeypatch.setattr(stripe.Subscription, "modify", lambda *a, **k: object())  # noqa: ARG005
    admin_client = await _as_superadmin(client, session)
    response = await admin_client.post(
        f"/api/v1/admin/orgs/{org.id}/extend-trial", json={"days": 7}
    )
    assert response.status_code == 200

    extended = await SubscriptionRepository(session).get_by_org_id(org.id)
    assert extended is not None
    new_trial_end = extended.trial_end
    assert new_trial_end is not None

    webhook_event = WebhookEvent(
        id=f"evt_{uuid4().hex[:8]}",
        type="customer.subscription.updated",
        payload={
            "data": {
                "object": {
                    "id": stripe_subscription_id,
                    "status": "trialing",
                    "items": {"data": [{"price": {"id": "price_m"}}]},
                    "current_period_start": int(
                        (datetime.now(UTC) - timedelta(days=10)).timestamp()
                    ),
                    "current_period_end": int((datetime.now(UTC) + timedelta(days=20)).timestamp()),
                    "cancel_at_period_end": False,
                    "trial_end": int(new_trial_end.timestamp()),
                    "metadata": {"org_id": str(org.id)},
                }
            }
        },
    )

    await BillingService(session, redis).handle_event(webhook_event)
    await session.commit()

    reconciled = await SubscriptionRepository(session).get_by_org_id(org.id)
    assert reconciled is not None
    assert reconciled.trial_end is not None
    assert abs((reconciled.trial_end - new_trial_end).total_seconds()) < 1


# ---- POST /orgs/{id}/comp-credit ----------------------------------------------


async def test_comp_credit_denies_non_superadmin(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    org = await _make_org(session)
    await session.commit()
    admin_client = await _as_non_superadmin(client, session)

    response = await admin_client.post(
        f"/api/v1/admin/orgs/{org.id}/comp-credit",
        json={"amount_cents": 500, "reason": "Service outage"},
    )

    assert response.status_code == 403


async def test_comp_credit_returns_404_without_billable_subscription(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    org = await _make_org(session)
    await session.commit()
    admin_client = await _as_superadmin(client, session)

    response = await admin_client.post(
        f"/api/v1/admin/orgs/{org.id}/comp-credit",
        json={"amount_cents": 500, "reason": "Service outage"},
    )

    assert response.status_code == 404


async def test_comp_credit_issues_coupon_and_writes_audit_event(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = await _make_org(session)
    plan = await _make_plan(session)
    stripe_subscription_id = f"sub_real_{uuid4().hex[:8]}"
    await _make_subscription(session, org, plan, stripe_subscription_id=stripe_subscription_id)
    await session.commit()

    coupon_calls: dict[str, Any] = {}
    modify_calls: dict[str, Any] = {}

    class _FakeCoupon:
        id = "coupon_123"

    def fake_coupon_create(**kwargs: object) -> _FakeCoupon:
        coupon_calls.update(kwargs)
        return _FakeCoupon()

    def fake_subscription_modify(sub_id: str, **kwargs: object) -> object:
        modify_calls["id"] = sub_id
        modify_calls["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(stripe.Coupon, "create", fake_coupon_create)
    monkeypatch.setattr(stripe.Subscription, "modify", fake_subscription_modify)
    admin_client = await _as_superadmin(client, session)

    response = await admin_client.post(
        f"/api/v1/admin/orgs/{org.id}/comp-credit",
        json={"amount_cents": 1500, "reason": "Service outage credit"},
    )

    assert response.status_code == 200
    assert coupon_calls["amount_off"] == 1500
    assert modify_calls["id"] == stripe_subscription_id
    assert modify_calls["kwargs"]["discounts"] == [{"coupon": "coupon_123"}]

    events, _ = await AuditRepository(session).list_cursor(org.id)
    matching = [e for e in events if e.action == "admin.org.comp_credit"]
    assert len(matching) == 1
    assert matching[0].severity == "elevated"
    diff = matching[0].diff
    assert diff is not None
    assert diff["amount_cents"] == 1500
    assert diff["coupon_id"] == "coupon_123"


async def test_comp_credit_missing_reason_returns_422(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    org = await _make_org(session)
    await session.commit()
    admin_client = await _as_superadmin(client, session)

    response = await admin_client.post(
        f"/api/v1/admin/orgs/{org.id}/comp-credit", json={"amount_cents": 500, "reason": ""}
    )

    assert response.status_code == 422
