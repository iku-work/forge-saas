"""
T-240's actual deliverable: proof that every write in the invalidation matrix
(app.core.cache_keys' docstring) busts the key it is supposed to bust, plus
the two claims that motivate the whole task -- the cached read really does
skip the database, and a deactivated account really does stop working on the
*next* request rather than a TTL later.

Each matrix row below drives the real write path -- an HTTP request against
the app, or the actual job function for the two worker-side rows -- not a
service call with a hand-rolled cache poke. A test that invalidates the cache
itself and then asserts the cache is empty proves nothing; what has to be
proven is that production code paths remember to.

Every row warms its key first and then asserts the key is *gone*. That makes
the assertion unambiguous in both directions: a write path that forgets to
invalidate leaves the payload behind, and one that invalidates but then
re-reads through the cache leaves a fresh payload behind -- either way the
key is still there.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pyotp
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.core import cache, cache_keys
from app.core.dependencies import get_current_user
from app.models._mixins import utcnow
from app.modules.auth.models import User
from app.modules.auth.permissions import MemberRole
from app.modules.auth.repository import (
    EmailVerificationTokenRepository,
    PasswordResetTokenRepository,
    TOTPSecretRepository,
    UserRepository,
)
from app.modules.auth.utils import create_access_token, generate_refresh_token, hash_password
from app.modules.billing.dunning import DunningService
from app.modules.billing.models import Plan, Subscription, WebhookEvent
from app.modules.billing.repository import SubscriptionRepository
from app.modules.billing.service import BillingService
from app.modules.tenancy.models import Membership, Organization
from app.modules.tenancy.repository import MembershipRepository, OrganizationRepository
from app.workers.jobs import billing as billing_job_module
from app.workers.jobs import gdpr as gdpr_job_module
from app.workers.jobs.billing import expire_trials_to_inactive, send_dunning_email
from app.workers.jobs.gdpr import purge_user_data
from tests._auth import session_token
from tests.fixtures.query_counter import assert_max_queries

# T-291: this file needs billing, notifications and file_storage enabled --
# their routes, their tables, or both. CI's module-matrix job deselects it in
# the combination that turns every optional module off.
pytestmark = pytest.mark.module_dependent

_PASSWORD = "Correct-Horse9"
_NEW_PASSWORD = "Correct-Horse10"


@pytest.fixture(autouse=True)
def _jobs_use_the_test_session(monkeypatch: pytest.MonkeyPatch, session: AsyncSession) -> None:
    """The two worker rows of the matrix open their own session via
    async_session_factory, which would reach the database on a *different*
    connection -- and conftest's fixtures live inside an uncommitted
    transaction, so that connection sees none of the test's data. Point the
    factory at the test session instead (the same trick test_dunning_job.py
    uses, minus its bespoke engine)."""

    @asynccontextmanager
    async def _factory() -> AsyncIterator[AsyncSession]:
        yield session

    monkeypatch.setattr(billing_job_module, "async_session_factory", _factory)
    monkeypatch.setattr(gdpr_job_module, "async_session_factory", _factory)


# --- world ------------------------------------------------------------------


@dataclass
class World:
    """One org, its owner, a second member, a superadmin, and a paid plan --
    enough for every row of the matrix without each row rebuilding it."""

    client: httpx.AsyncClient
    session: AsyncSession
    redis: Redis
    owner: User
    member: User
    superadmin: User
    org: Organization
    member_membership: Membership
    plan: Plan


async def _make_user(session: AsyncSession, **overrides: Any) -> User:  # noqa: ANN401 -- forwarded to UserRepository.create
    return await UserRepository(session).create(
        email=f"user-{uuid4()}@example.com",
        hashed_password=hash_password(_PASSWORD),
        email_verified_at=datetime.now(UTC),
        **overrides,
    )


@pytest.fixture
async def world(client: httpx.AsyncClient, session: AsyncSession, redis: Redis) -> World:
    owner = await _make_user(session)
    member = await _make_user(session)
    superadmin = await _make_user(session, is_superadmin=True, totp_enabled=True)
    org = await OrganizationRepository(session).create(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    memberships = MembershipRepository(session)
    await memberships.create(user_id=owner.id, org_id=org.id, role=MemberRole.OWNER)
    member_membership = await memberships.create(
        user_id=member.id, org_id=org.id, role=MemberRole.MEMBER
    )
    plan = Plan(
        name="Pro",
        slug=f"pro-{uuid4().hex[:8]}",
        stripe_price_id_monthly="price_pro_m",
        stripe_price_id_yearly="price_pro_y",
        price_monthly=2900,
        price_yearly=29000,
        features={"storage_gb": 50},
    )
    session.add(plan)
    await session.commit()

    client.headers["Authorization"] = f"Bearer {create_access_token(owner.id, None, ())}"
    client.headers["X-Org-ID"] = str(org.id)
    return World(
        client=client,
        session=session,
        redis=redis,
        owner=owner,
        member=member,
        superadmin=superadmin,
        org=org,
        member_membership=member_membership,
        plan=plan,
    )


def _as(world: World, user: User) -> None:
    world.client.headers["Authorization"] = f"Bearer {create_access_token(user.id, None, ())}"


async def _as_superadmin(world: World) -> None:
    # A session that passed a second factor: require_superadmin reads the
    # session's methods, not the account's enrollment (review R3).
    token = await session_token(world.session, world.superadmin)
    world.client.headers["Authorization"] = f"Bearer {token}"


def _payload_for(world: World, key: str) -> cache.JsonDict:
    """A *realistic* payload for whichever key a row seeds.

    Not a sentinel: the write paths below are real authenticated requests,
    and get_current_user / get_current_org hydrate a model straight out of
    whatever this leaves in Redis -- a `{"sentinel": ...}` payload makes the
    request 404 on a user with no id rather than exercising the write. The
    assertion doesn't need a marker value anyway: it is that the key is
    *absent* afterwards, which a re-populating read would defeat just as
    surely as a missing invalidation."""
    if key == cache_keys.user_key(world.owner.id):
        return cache.dump(world.owner)
    if key == cache_keys.user_key(world.member.id):
        return cache.dump(world.member)
    if key == cache_keys.org_settings_key(world.org.id):
        return cache.dump(world.org)
    if key == cache_keys.org_plan_key(world.org.id):
        return {"subscription": None, "plan": None}
    raise AssertionError(f"no seed payload defined for {key}")


async def _seed(world: World, key: str) -> None:
    payload = _payload_for(world, key)
    await cache.write_json(world.redis, key, payload, ttl=300)
    assert await cache.read_json(world.redis, key) == payload


async def _make_subscription(
    world: World,
    *,
    status: str = "active",
    **overrides: Any,  # noqa: ANN401 -- forwarded to the Subscription ctor
) -> Subscription:
    fields: dict[str, Any] = {
        "org_id": world.org.id,
        "plan_id": world.plan.id,
        "stripe_subscription_id": f"sub_{uuid4().hex[:8]}",
        "status": status,
        "current_period_start": utcnow() - timedelta(days=10),
        "current_period_end": utcnow() + timedelta(days=20),
        **overrides,
    }
    subscription = Subscription(**fields)
    world.session.add(subscription)
    await world.session.commit()
    return subscription


# --- the matrix -------------------------------------------------------------
#
# Each row: (id, the key that must be busted, the write path that must bust it).


async def _write_profile_update(world: World) -> None:
    response = await world.client.patch("/api/v1/auth/me", json={"locale": "fi"})
    assert response.status_code == 200


async def _write_notification_preferences(world: World) -> None:
    response = await world.client.patch(
        "/api/v1/me/notification-preferences",
        json={"preferences": {"payment_failed": {"in_app": True, "email": False, "push": False}}},
    )
    assert response.status_code == 200


async def _write_change_password(world: World) -> None:
    response = await world.client.post(
        "/api/v1/auth/change-password",
        json={"current_password": _PASSWORD, "new_password": _NEW_PASSWORD},
    )
    assert response.status_code == 200


async def _write_reset_password(world: World) -> None:
    plain, token_hash = generate_refresh_token()
    await PasswordResetTokenRepository(world.session).create(
        user_id=world.owner.id, token_hash=token_hash, expires_at=utcnow() + timedelta(hours=1)
    )
    await world.session.commit()

    response = await world.client.post(
        "/api/v1/auth/reset-password", json={"token": plain, "new_password": _NEW_PASSWORD}
    )
    assert response.status_code == 200


async def _write_verify_email(world: World) -> None:
    plain, token_hash = generate_refresh_token()
    await EmailVerificationTokenRepository(world.session).create(
        user_id=world.owner.id, token_hash=token_hash, expires_at=utcnow() + timedelta(hours=1)
    )
    await world.session.commit()

    response = await world.client.post("/api/v1/auth/verify-email", json={"token": plain})
    assert response.status_code == 200


async def _write_enable_totp(world: World) -> None:
    enable = await world.client.post("/api/v1/auth/2fa/enable")
    assert enable.status_code == 200
    secret = enable.json()["data"]["secret"]

    verify = await world.client.post(
        "/api/v1/auth/2fa/verify", json={"code": pyotp.TOTP(secret).now()}
    )
    assert verify.status_code == 200


async def _write_disable_totp(world: World) -> None:
    await _write_enable_totp(world)
    await _seed(world, cache_keys.user_key(world.owner.id))  # re-seed: enable busted it too
    secret_record = await TOTPSecretRepository(world.session).get_by_user_id(world.owner.id)
    assert secret_record is not None

    # One step ahead of the code /2fa/verify just consumed: T-218 rejects a
    # replay of the same step, and both calls land inside the same 30s window.
    next_step_code = pyotp.TOTP(secret_record.secret_encrypted).at(utcnow() + timedelta(seconds=30))
    response = await world.client.post(
        "/api/v1/auth/2fa/disable",
        json={"password": _PASSWORD, "code": next_step_code},
    )
    assert response.status_code == 200


async def _write_admin_disable_totp(world: World) -> None:
    await _write_enable_totp(world)
    await _seed(world, cache_keys.user_key(world.owner.id))
    await _as_superadmin(world)

    response = await world.client.post(
        f"/api/v1/admin/users/{world.owner.id}/disable-totp",
        json={"reason": "User lost their phone and all backup codes (ticket #4821)"},
    )
    assert response.status_code == 200


async def _write_gdpr_delete(world: World) -> None:
    response = await world.client.request("DELETE", "/api/v1/me", json={"confirm": "DELETE"})
    assert response.status_code == 202


async def _write_role_change(world: World) -> None:
    response = await world.client.patch(
        f"/api/v1/orgs/{world.org.id}/members/{world.member_membership.id}",
        json={"role": "admin"},
    )
    assert response.status_code == 200


async def _write_remove_member(world: World) -> None:
    response = await world.client.delete(
        f"/api/v1/orgs/{world.org.id}/members/{world.member_membership.id}"
    )
    assert response.status_code == 200


async def _write_org_update(world: World) -> None:
    response = await world.client.patch(
        f"/api/v1/orgs/{world.org.id}", json={"name": "Acme Renamed"}
    )
    assert response.status_code == 200


async def _write_subscription_webhook(world: World) -> None:
    event = WebhookEvent(
        id=f"evt_{uuid4().hex[:8]}",
        type="customer.subscription.created",
        payload={
            "data": {
                "object": {
                    "id": f"sub_{uuid4().hex[:8]}",
                    "status": "active",
                    "metadata": {"org_id": str(world.org.id)},
                    "items": {"data": [{"price": {"id": world.plan.stripe_price_id_monthly}}]},
                    "current_period_start": 1_700_000_000,
                    "current_period_end": 1_702_592_000,
                    "cancel_at_period_end": False,
                    "trial_end": None,
                }
            }
        },
    )
    await BillingService(world.session, world.redis).handle_event(event)
    await world.session.commit()


async def _write_admin_plan_override(world: World) -> None:
    await _as_superadmin(world)
    response = await world.client.put(
        f"/api/v1/admin/orgs/{world.org.id}/plan",
        json={"plan_slug": world.plan.slug, "free_override": True},
    )
    assert response.status_code == 200


async def _write_admin_extend_trial(world: World) -> None:
    await _make_subscription(
        world,
        status="trialing",
        stripe_subscription_id=f"admin_override_{uuid4()}",
        trial_end=utcnow() + timedelta(days=3),
    )
    await _as_superadmin(world)

    response = await world.client.post(
        f"/api/v1/admin/orgs/{world.org.id}/extend-trial", json={"days": 14}
    )
    assert response.status_code == 200


async def _write_feature_flag_toggle(world: World) -> None:
    await _as_superadmin(world)
    response = await world.client.put(
        f"/api/v1/admin/orgs/{world.org.id}/feature-flags/storage_gb", json={"enabled": False}
    )
    assert response.status_code == 200


async def _write_dunning_downgrade(world: World) -> None:
    subscription = await _make_subscription(world, status="past_due")
    campaign = await DunningService(world.session).start(subscription.id, "in_test")
    await world.session.commit()

    await send_dunning_email({"redis": world.redis}, campaign_id=campaign.id, step="day7")

    refetched = await SubscriptionRepository(world.session).get_by_id(subscription.id)
    assert refetched is not None
    assert refetched.status == "canceled"  # the downgrade actually happened


async def _write_trial_expiry_sweep(world: World) -> None:
    await _make_subscription(
        world,
        status="trialing",
        trial_end=utcnow() - timedelta(days=1),
    )
    await expire_trials_to_inactive({"redis": world.redis})


async def _write_purge_job(world: World) -> None:
    # Remove the other member first: _detach_orphaned_org_billing (and the
    # org-cache invalidation it carries) only fires for an org left with no
    # memberships at all.
    await MembershipRepository(world.session).remove(world.member_membership.id)
    await UserRepository(world.session).update(world.owner.id, deleted_at=utcnow())
    await world.session.commit()

    await purge_user_data({"redis": world.redis}, user_id=world.owner.id)


_MATRIX: list[tuple[str, Callable[[World], str], Callable[[World], Awaitable[None]]]] = [
    # --- user cache ---
    ("profile_update", lambda w: cache_keys.user_key(w.owner.id), _write_profile_update),
    (
        "notification_preferences",
        lambda w: cache_keys.user_key(w.owner.id),
        _write_notification_preferences,
    ),
    ("change_password", lambda w: cache_keys.user_key(w.owner.id), _write_change_password),
    ("reset_password", lambda w: cache_keys.user_key(w.owner.id), _write_reset_password),
    ("verify_email", lambda w: cache_keys.user_key(w.owner.id), _write_verify_email),
    ("totp_enabled", lambda w: cache_keys.user_key(w.owner.id), _write_enable_totp),
    ("totp_disabled", lambda w: cache_keys.user_key(w.owner.id), _write_disable_totp),
    (
        "totp_disabled_by_admin",
        lambda w: cache_keys.user_key(w.owner.id),
        _write_admin_disable_totp,
    ),
    ("gdpr_delete", lambda w: cache_keys.user_key(w.owner.id), _write_gdpr_delete),
    ("gdpr_purge_job", lambda w: cache_keys.user_key(w.owner.id), _write_purge_job),
    ("role_change", lambda w: cache_keys.user_key(w.member.id), _write_role_change),
    ("member_removed", lambda w: cache_keys.user_key(w.member.id), _write_remove_member),
    # --- org settings cache ---
    ("org_update", lambda w: cache_keys.org_settings_key(w.org.id), _write_org_update),
    ("org_purge_detach", lambda w: cache_keys.org_settings_key(w.org.id), _write_purge_job),
    # --- plan / entitlement cache ---
    (
        "subscription_webhook",
        lambda w: cache_keys.org_plan_key(w.org.id),
        _write_subscription_webhook,
    ),
    (
        "admin_plan_override",
        lambda w: cache_keys.org_plan_key(w.org.id),
        _write_admin_plan_override,
    ),
    ("admin_extend_trial", lambda w: cache_keys.org_plan_key(w.org.id), _write_admin_extend_trial),
    (
        "feature_flag_toggle",
        lambda w: cache_keys.org_plan_key(w.org.id),
        _write_feature_flag_toggle,
    ),
    ("dunning_downgrade", lambda w: cache_keys.org_plan_key(w.org.id), _write_dunning_downgrade),
    ("trial_expiry_sweep", lambda w: cache_keys.org_plan_key(w.org.id), _write_trial_expiry_sweep),
]


@pytest.mark.parametrize(
    ("key_for", "write"),
    [pytest.param(key_for, write, id=row_id) for row_id, key_for, write in _MATRIX],
)
async def test_write_path_busts_its_cache_key(
    world: World,
    key_for: Callable[[World], str],
    write: Callable[[World], Awaitable[None]],
) -> None:
    key = key_for(world)
    await _seed(world, key)

    await write(world)

    assert await cache.read_json(world.redis, key) is None, (
        f"{key} still holds a stale payload after the write"
    )


# --- the two claims the matrix exists to support ----------------------------


async def test_second_get_current_user_issues_zero_db_queries(world: World) -> None:
    """The point of the cache: the hot read is free the second time."""
    token = create_access_token(world.owner.id, None, ())
    request = Request(scope={"type": "http"})

    await get_current_user(
        request=request, token=token, session=world.session, redis=world.redis
    )  # warms

    async with assert_max_queries(world.session, 0):
        user = await get_current_user(
            request=request, token=token, session=world.session, redis=world.redis
        )

    assert user.id == world.owner.id
    assert isinstance(user.id, UUID)  # hydrated, not a raw JSON string


async def test_deactivated_user_is_rejected_on_the_very_next_request(world: World) -> None:
    """The security-relevant half of the matrix: GDPR delete-me sets
    deleted_at, and the account must stop working immediately -- not once the
    60s TTL happens to lapse."""
    me = await world.client.get("/api/v1/auth/me")
    assert me.status_code == 200  # cache now warm

    deleted = await world.client.request("DELETE", "/api/v1/me", json={"confirm": "DELETE"})
    assert deleted.status_code == 202

    after = await world.client.get("/api/v1/auth/me")
    assert after.status_code == 401


async def test_org_row_is_served_from_cache_on_the_second_request(world: World) -> None:
    """get_current_org's half: the second org-scoped request re-reads the
    membership (never cached -- it is the tenant-isolation check) but not the
    organization row."""
    key = cache_keys.org_settings_key(world.org.id)
    assert await world.redis.get(key) is None

    first = await world.client.get(f"/api/v1/orgs/{world.org.id}")
    assert first.status_code == 200

    # The org row is only cached by get_current_org (X-Org-ID), which the
    # path-keyed /orgs/{id} route does not use -- warm it through a route
    # that does.
    warmed = await world.client.get("/api/v1/notifications/unread-count")
    assert warmed.status_code == 200
    assert await cache.read_json(world.redis, key) is not None

    # A direct DB rename that skips the invalidation path stays invisible --
    # which is exactly why every org write above has to bust the key.
    await OrganizationRepository(world.session).update(
        world.org.id, name="Renamed Behind The Cache"
    )
    await world.session.commit()

    org_from_cache = cache.hydrate(Organization, await cache.read_json(world.redis, key) or {})
    assert org_from_cache.name == "Acme"
