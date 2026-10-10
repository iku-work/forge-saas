"""
T-243: every list endpoint is proven to issue a *constant* number of SQL
statements regardless of how many rows it returns.

The property, not a magic number. Each endpoint below is hit twice against
the same world: once with 3 rows seeded and once with 30. If the two requests
issue a different number of statements, something in the page loop is talking
to the database per row -- which is exactly what an N+1 is, and what the
failure message prints (every captured statement, via
tests/fixtures/query_counter.py, so the repeated SELECT is visible rather
than just a count that went from 7 to 24).

BUDGETS on top of that is the second half: the constant is also asserted to
be *small*. A page that always issues 40 statements is constant and still
wrong, and a refactor that quietly adds a fixed extra round trip to a hot
list is worth seeing in a diff. `test_every_list_route_has_a_budget` fails
when a new `list_`-style route is added without an entry, so this file can't
silently fall behind the router (the T-049 sweep pattern).

Redis is flushed before each measurement (T-240 caches the current user, org,
entitlements, the invoice page, feature flags): both measurements then start
from the same cold cache and are directly comparable, and the cold path is
where an N+1 actually bites.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import iter_route_contexts
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.routing import BaseRoute

from app.core.module_loader import is_enabled
from app.main import app
from app.models._mixins import utcnow
from app.modules.api_keys.models import ApiKey
from app.modules.audit.models import AuditEvent
from app.modules.auth.models import OAuthAccount, User, WebAuthnCredential
from app.modules.auth.models import Session as SessionModel
from app.modules.auth.permissions import MemberRole
from app.modules.auth.repository import UserRepository
from app.modules.auth.utils import create_access_token, hash_password
from app.modules.billing import invoices as invoices_module
from app.modules.billing.models import Plan, Subscription
from app.modules.gdpr.models import ConsentRecord
from app.modules.notifications.models import Notification
from app.modules.storage.models import StoredFile
from app.modules.tenancy.models import Organization, OrgFeatureFlag
from app.modules.tenancy.repository import (
    InvitationRepository,
    MembershipRepository,
    OrganizationRepository,
)
from app.modules.webhooks.events import ALL_EVENT_TYPES
from app.modules.webhooks.models import WebhookDelivery, WebhookEndpoint
from tests._auth import session_token
from tests.fixtures.query_counter import QueryLog, count_queries

# T-291: this file needs every module that owns a list endpoint -- the shared
# seed alone writes a Plan, and half of ENDPOINTS is module-owned. CI's
# module-matrix job deselects it in the combination that turns every optional
# module off.
pytestmark = pytest.mark.module_dependent

_PASSWORD = "Correct-Horse9"

# Endpoint function name -> the most SQL statements one page may issue.
#
# Every number here was measured, not guessed: it is the observed constant
# (auth/org/entitlement dependencies included), with no headroom added. Raise
# one only with a reason -- the count is per *request*, so a "+1" is a new
# round trip on a hot path for every user of that list.
# T-263 added exactly one statement to every non-exempt route: the maintenance
# middleware reads the ProductSettings singleton before the router runs. Every
# budget below that is not under /api/v1/admin/* therefore went up by one, and
# the increase is worth understanding rather than just absorbing:
#
#   * It is one indexed primary-key lookup, and it is *shared*: the row is
#     cached in Redis for 60s (app.modules.product_settings.service), so in
#     production it is one DB read per minute per deployment, not one per
#     request. This suite flushes Redis before each measurement precisely to
#     see the cold path, which is why it shows up here at all.
#   * It is constant. The property this file actually defends -- the same
#     statement count for 3 rows and for 30 -- is untouched.
#   * The three admin budgets are unchanged, because /api/v1/admin/* is on the
#     middleware's exemption list (an operator must be able to end a
#     maintenance window). That asymmetry is a useful check in itself: if an
#     admin budget ever needs +1 for this reason, the exemption has broken.
BUDGETS: dict[str, int] = {
    # 1 for the maintenance check + 1 for get_current_user + 1 for the page.
    "list_sessions": 3,
    "list_oauth_accounts": 3,
    "list_credentials": 3,
    "list_consents": 3,
    "list_my_orgs": 3,
    # Org-scoped: + the org row and the membership (the tenant-isolation
    # check, deliberately never cached -- see get_current_org).
    "list_invitations": 5,
    # One fewer than its neighbours, and the difference is T-311's: this route
    # now resolves its principal through `require_scope`, which reads the
    # membership (the isolation check) but *not* the Organization row -- which
    # the endpoint never used. Opening a route to API keys made it cheaper for
    # sessions too, which is the sort of thing worth noticing rather than
    # absorbing. +2 since the visual overhaul: the page's users in one batched
    # `IN` (names, emails, photos on each row) and the COUNT behind meta.total.
    "list_members": 6,
    "list_notifications": 5,
    # + the org's subscription/plan behind the feature-flag map.
    "list_feature_flags": 6,
    # Measured: product_settings (the maintenance check), "user"
    # (get_current_user), organization + membership (get_current_org and
    # require_permission), and the page of stored_file. Unlike list_members,
    # this route declares get_current_org and so does read the Organization
    # row. It was 7 while require_feature("storage_gb")
    # gated the route -- two statements spent on a question it no longer asks.
    # +1 since the visual overhaul: the page's uploaders in one batched `IN`.
    "list_files": 6,
    # Stripe-backed (T-133): only the maintenance check and the auth/org
    # dependencies touch the DB.
    "list_invoices": 4,
    # Unauthenticated public price list -- the maintenance check and one
    # SELECT, nothing else.
    "list_plans": 2,
    # Superadmin-gated (T-141/T-143), and exempt from the maintenance check
    # above. One read is the session row: since review R3 the admin gate
    # needs a token bound to a session that passed a second factor, and the
    # revocation check every session-bound token pays (resolve_user_from_token)
    # loads that row -- require_superadmin reads its `amr` from there rather
    # than adding a query. The audit feed's two extra reads are T-243's
    # batched actor/org lookups, which used to be one query per distinct actor
    # and org.
    "list_orgs": 3,
    "list_users": 3,
    "list_admin_audit_feed": 5,
}

# T-310. `public_api` ships off, so at the committed configuration
# `list_api_keys` is not a route in this process and must not have a budget --
# `test_every_budget_has_a_covering_endpoint_spec` pairs BUDGETS with ENDPOINTS
# exactly, and an entry for a route that does not exist would be a spec that
# can never run. In a product that turns the module on (and in CI's
# module-matrix combination C) the route *is* registered, the meta-check below
# demands a budget for it, and this is where it comes from. One condition
# rather than two: the endpoint spec is added under the same `if` further
# down, so the two halves cannot drift.
if is_enabled("public_api"):
    # Same shape as list_members: the maintenance check, get_current_user, the
    # org row and the membership behind require_permission, and the page.
    BUDGETS["list_api_keys"] = 5

# T-320, and the same conditional for the same reason: `outbound_webhooks`
# ships off, so at the committed configuration neither route is registered in
# this process and neither may carry a budget.
if is_enabled("outbound_webhooks"):
    # As list_api_keys.
    BUDGETS["list_endpoints"] = 5
    # + the endpoint row, read before the log is paged so a delivery list is
    # not reachable by guessing an endpoint id (§0.2). The property this file
    # defends is that it stays 6 for 10,000 deliveries as well as for 3.
    BUDGETS["list_deliveries"] = 6
    # The event catalogue is rendered from a dict in code and touches no table
    # of its own -- only the maintenance check, get_current_user, the org row
    # and the membership. That it is *not* higher is the point of listing it.
    BUDGETS["list_event_types"] = 4

_SEED_FIRST = 3
_SEED_SECOND = 27  # 3 + 27 = 30, past the default page cap of 20


# --- world ------------------------------------------------------------------


@dataclass
class World:
    """One org with an owner, a paid subscription and a superadmin -- enough
    to reach every list endpoint without each spec rebuilding it."""

    client: httpx.AsyncClient
    session: AsyncSession
    redis: Redis
    owner: User
    superadmin: User
    org: Organization
    plan: Plan
    # T-133 invoices live in Stripe, not in a table -- this is that endpoint's
    # "rows", grown by _seed_stripe_invoices and served by the stub below.
    stripe_invoices: list[SimpleNamespace] = field(default_factory=list)


async def _make_user(session: AsyncSession, **overrides: Any) -> User:  # noqa: ANN401 -- forwarded to UserRepository.create
    return await UserRepository(session).create(
        email=f"user-{uuid4()}@example.com",
        hashed_password=hash_password(_PASSWORD),
        email_verified_at=datetime.now(UTC),
        **overrides,
    )


@pytest.fixture
async def world(
    client: httpx.AsyncClient,
    session: AsyncSession,
    redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> World:
    owner = await _make_user(session)
    # T-218: require_superadmin also gates on a second factor being enrolled.
    superadmin = await _make_user(session, is_superadmin=True, totp_enabled=True)
    org = await OrganizationRepository(session).create(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    org.stripe_customer_id = f"cus_{uuid4().hex[:8]}"  # so list_invoices reaches Stripe
    await MembershipRepository(session).create(
        user_id=owner.id, org_id=org.id, role=MemberRole.OWNER
    )
    plan = Plan(
        name="Pro",
        slug=f"pro-{uuid4().hex[:8]}",
        stripe_price_id_monthly="price_pro_m",
        stripe_price_id_yearly="price_pro_y",
        price_monthly=2900,
        price_yearly=29000,
        features={"storage_gb": 50},  # entitles list_files' require_feature gate
    )
    session.add(plan)
    await session.flush()
    session.add(
        Subscription(
            org_id=org.id,
            plan_id=plan.id,
            stripe_subscription_id=f"sub_{uuid4().hex[:8]}",
            status="active",
            current_period_start=utcnow() - timedelta(days=10),
            current_period_end=utcnow() + timedelta(days=20),
        )
    )
    await session.commit()

    client.headers["X-Org-ID"] = str(org.id)
    world = World(
        client=client,
        session=session,
        redis=redis,
        owner=owner,
        superadmin=superadmin,
        org=org,
        plan=plan,
    )

    # T-206 moved the real call behind app.modules.billing.invoices.
    # _list_stripe_invoices; stubbing it there keeps the surrounding cache
    # read/write (the part that does touch Redis) real.
    async def _fake_list(
        *,
        customer_id: str,  # noqa: ARG001 -- mirrors the real signature
        limit: int,
        after_cursor: str | None,  # noqa: ARG001 -- mirrors the real signature
    ) -> SimpleNamespace:
        page = world.stripe_invoices[:limit]
        return SimpleNamespace(data=page, has_more=len(world.stripe_invoices) > limit)

    monkeypatch.setattr(invoices_module, "_list_stripe_invoices", _fake_list)
    return world


# --- seeds ------------------------------------------------------------------
#
# Each seed *adds* `n` rows, so a spec can be measured at 3 and again at 30.


async def _seed_sessions(world: World, n: int) -> None:
    for _ in range(n):
        world.session.add(
            SessionModel(
                user_id=world.owner.id,
                refresh_token_hash=uuid4().hex,
                ip_address="203.0.113.1",
                user_agent="pytest",
                last_seen_at=utcnow(),
                expires_at=utcnow() + timedelta(days=30),
                auth_time=utcnow(),
            )
        )
    await world.session.commit()


async def _seed_oauth_accounts(world: World, n: int) -> None:
    for _ in range(n):
        world.session.add(
            OAuthAccount(user_id=world.owner.id, provider="google", provider_user_id=uuid4().hex)
        )
    await world.session.commit()


async def _seed_webauthn_credentials(world: World, n: int) -> None:
    for _ in range(n):
        world.session.add(
            WebAuthnCredential(
                user_id=world.owner.id,
                credential_id=uuid4().hex,
                public_key="cHVibGljLWtleQ==",
                aaguid="00000000-0000-0000-0000-000000000000",
            )
        )
    await world.session.commit()


async def _seed_consents(world: World, n: int) -> None:
    for _ in range(n):
        world.session.add(
            ConsentRecord(
                user_id=world.owner.id,
                consent_type="marketing",
                granted=True,
                version="2026-01",
                ip_address="203.0.113.1",
                user_agent="pytest",
            )
        )
    await world.session.commit()


async def _seed_orgs_for_owner(world: World, n: int) -> None:
    memberships = MembershipRepository(world.session)
    orgs = OrganizationRepository(world.session)
    for _ in range(n):
        org = await orgs.create(name="Other", slug=f"other-{uuid4().hex[:8]}")
        await memberships.create(user_id=world.owner.id, org_id=org.id, role=MemberRole.MEMBER)
    await world.session.commit()


async def _seed_members(world: World, n: int) -> None:
    memberships = MembershipRepository(world.session)
    for _ in range(n):
        user = await _make_user(world.session)
        await memberships.create(user_id=user.id, org_id=world.org.id, role=MemberRole.MEMBER)
    await world.session.commit()


async def _seed_invitations(world: World, n: int) -> None:
    invitations = InvitationRepository(world.session)
    for _ in range(n):
        await invitations.create(
            invitation_id=uuid4(),
            org_id=world.org.id,
            email=f"invitee-{uuid4().hex[:8]}@example.com",
            role=MemberRole.MEMBER,
            token_hash=uuid4().hex,
            invited_by=world.owner.id,
            expires_at=utcnow() + timedelta(days=7),
        )
    await world.session.commit()


async def _seed_notifications(world: World, n: int) -> None:
    for _ in range(n):
        world.session.add(
            Notification(
                org_id=world.org.id,
                user_id=world.owner.id,
                type="member_invited",
                title="Invited",
                body="You were invited",
                params={"org_name": "Acme"},
                channels=["in_app"],
            )
        )
    await world.session.commit()


async def _seed_api_keys(world: World, n: int) -> None:
    """T-310. Rows are written directly rather than through ApiKeyService so
    the seed costs one insert per key instead of a digest and a Redis round
    trip -- what is being measured is the *read* path."""
    for index in range(n):
        world.session.add(
            ApiKey(
                org_id=world.org.id,
                name=f"key {index}",
                prefix=uuid4().hex[:12],
                key_hash="0" * 64,
                scopes=["orgs:read"],
                created_by=world.owner.id,
            )
        )
    await world.session.commit()


# T-320. Deliveries need an endpoint to hang off, and the endpoint has to
# exist before the first delivery is seeded -- a module-level fixture cannot
# hold it (the org is built per test), so the seeds share this lazily-created
# one through the World they are handed.
_WEBHOOK_ENDPOINTS: dict[UUID, WebhookEndpoint] = {}


async def _webhook_endpoint(world: World) -> WebhookEndpoint:
    existing = _WEBHOOK_ENDPOINTS.get(world.org.id)
    if existing is not None:
        return existing
    endpoint = WebhookEndpoint(
        org_id=world.org.id,
        url="https://consumer.example.com/hook",
        secret="whsec_seed",
        event_types=["member.invited"],
        created_by=world.owner.id,
    )
    world.session.add(endpoint)
    await world.session.commit()
    _WEBHOOK_ENDPOINTS[world.org.id] = endpoint
    return endpoint


async def _seed_webhook_endpoints(world: World, n: int) -> None:
    for index in range(n):
        world.session.add(
            WebhookEndpoint(
                org_id=world.org.id,
                url=f"https://consumer.example.com/hook/{index}",
                secret=f"whsec_seed_{index}",
                event_types=["member.invited"],
                created_by=world.owner.id,
            )
        )
    await world.session.commit()


async def _seed_webhook_deliveries(world: World, n: int) -> None:
    endpoint = await _webhook_endpoint(world)
    for _ in range(n):
        world.session.add(
            WebhookDelivery(
                org_id=world.org.id,
                endpoint_id=endpoint.id,
                event_type="member.invited",
                payload={"id": str(uuid4()), "type": "member.invited"},
                status="delivered",
                delivery_id=uuid4(),
            )
        )
    await world.session.commit()


async def _seed_event_types(world: World, n: int) -> None:  # noqa: ARG001 -- fixed catalogue
    """The event catalogue is a constant in code, so there is nothing to seed.

    Left as a real (empty) seeder rather than special-casing the sweep: the
    endpoint still has to prove it issues a constant number of queries, and
    the "the seed reached the response" assertion is satisfied because the
    catalogue is always longer than the seed count.
    """
    return


async def _seed_files(world: World, n: int) -> None:
    for _ in range(n):
        world.session.add(
            StoredFile(
                org_id=world.org.id,
                uploaded_by=world.owner.id,
                key=f"{world.org.id}/{uuid4().hex}",
                filename="report.pdf",
                content_type="application/pdf",
                size_bytes=1024,
                status="ready",
            )
        )
    await world.session.commit()


async def _seed_feature_flag_overrides(world: World, n: int) -> None:
    for i in range(n):
        world.session.add(
            OrgFeatureFlag(org_id=world.org.id, flag_name=f"flag_{uuid4().hex[:8]}_{i}")
        )
    await world.session.commit()


async def _seed_plans(world: World, n: int) -> None:
    for i in range(n):
        world.session.add(
            Plan(
                name=f"Plan {i}",
                slug=f"plan-{uuid4().hex[:8]}",
                stripe_price_id_monthly="price_m",
                stripe_price_id_yearly="price_y",
                price_monthly=100 + i,
                price_yearly=1000 + i,
                features={},
            )
        )
    await world.session.commit()


async def _seed_stripe_invoices(world: World, n: int) -> None:
    now = int(utcnow().timestamp())
    for _ in range(n):
        world.stripe_invoices.append(
            SimpleNamespace(
                id=f"in_{uuid4().hex[:12]}",
                number="INV-1",
                amount_paid=2900,
                currency="usd",
                status="paid",
                hosted_invoice_url="https://stripe.example/i",
                invoice_pdf="https://stripe.example/i.pdf",
                period_start=now,
                period_end=now,
                created=now,
            )
        )


async def _seed_directory_orgs(world: World, n: int) -> None:
    orgs = OrganizationRepository(world.session)
    for _ in range(n):
        await orgs.create(name="Directory", slug=f"dir-{uuid4().hex[:8]}")
    await world.session.commit()


async def _seed_directory_users(world: World, n: int) -> None:
    for _ in range(n):
        await _make_user(world.session)
    await world.session.commit()


async def _seed_audit_events(world: World, n: int) -> None:
    # A *distinct* actor and org per event on purpose: the pre-T-243 feed
    # resolved actor_email/org_name with one get_by_id per distinct id, which
    # a page of repeated actors would have hidden.
    orgs = OrganizationRepository(world.session)
    for _ in range(n):
        actor = await _make_user(world.session)
        org = await orgs.create(name="Audited", slug=f"aud-{uuid4().hex[:8]}")
        world.session.add(
            AuditEvent(
                action="org.updated",
                resource_type="organization",
                resource_id=str(org.id),
                org_id=org.id,
                actor_id=actor.id,
            )
        )
    await world.session.commit()


# --- the endpoints ----------------------------------------------------------


@dataclass(frozen=True)
class ListEndpoint:
    """One row of the sweep: how to reach a list endpoint and how to grow it."""

    name: str  # the route's endpoint function name -- the BUDGETS key
    path: str | Callable[[World], str]
    seed: Callable[[World, int], Awaitable[None]]
    as_superadmin: bool = False
    # Cursor-paginated endpoints cap the page; unbounded ones return everything.
    page_cap: int | None = 20
    # Where the countable items live in the envelope's `data`.
    items: Callable[[Any], Any] = field(default=lambda data: data)

    def url(self, world: World) -> str:
        return self.path(world) if callable(self.path) else self.path


# Every `list_`-style GET route in the app, which
# test_every_list_route_has_a_budget keeps true.
ENDPOINTS: list[ListEndpoint] = [
    ListEndpoint("list_sessions", "/api/v1/auth/sessions", _seed_sessions, page_cap=None),
    ListEndpoint(
        "list_oauth_accounts",
        "/api/v1/auth/oauth/accounts",
        _seed_oauth_accounts,
        page_cap=None,
    ),
    ListEndpoint(
        "list_credentials",
        "/api/v1/auth/webauthn/credentials",
        _seed_webauthn_credentials,
        page_cap=None,
    ),
    ListEndpoint("list_consents", "/api/v1/me/consents", _seed_consents, page_cap=None),
    ListEndpoint("list_my_orgs", "/api/v1/orgs", _seed_orgs_for_owner, page_cap=None),
    ListEndpoint(
        "list_members",
        lambda w: f"/api/v1/orgs/{w.org.id}/members",
        _seed_members,
    ),
    ListEndpoint(
        "list_invitations",
        lambda w: f"/api/v1/orgs/{w.org.id}/invitations",
        _seed_invitations,
    ),
    ListEndpoint("list_notifications", "/api/v1/notifications", _seed_notifications),
    ListEndpoint(
        "list_feature_flags",
        lambda w: f"/api/v1/orgs/{w.org.id}/feature-flags",
        _seed_feature_flag_overrides,
        page_cap=None,
        items=lambda data: data["flags"],
    ),
    ListEndpoint("list_files", "/api/v1/files", _seed_files),
    ListEndpoint("list_plans", "/api/v1/billing/plans", _seed_plans, page_cap=None),
    ListEndpoint("list_invoices", "/api/v1/billing/invoices", _seed_stripe_invoices),
    ListEndpoint("list_orgs", "/api/v1/admin/orgs", _seed_directory_orgs, as_superadmin=True),
    ListEndpoint("list_users", "/api/v1/admin/users", _seed_directory_users, as_superadmin=True),
    ListEndpoint(
        "list_admin_audit_feed", "/api/v1/admin/audit", _seed_audit_events, as_superadmin=True
    ),
]

if is_enabled("public_api"):
    ENDPOINTS.append(ListEndpoint("list_api_keys", "/api/v1/api-keys", _seed_api_keys))

if is_enabled("outbound_webhooks"):
    ENDPOINTS.extend(
        [
            ListEndpoint("list_endpoints", "/api/v1/webhooks/endpoints", _seed_webhook_endpoints),
            ListEndpoint(
                "list_event_types",
                "/api/v1/webhooks/event-types",
                _seed_event_types,
                # The catalogue is a constant in code, so it returns the same
                # rows however much data exists -- which is exactly what
                # `page_cap` means here, and what lets the sweep's "the seed
                # reached the response" assertion hold for an endpoint with
                # nothing to seed. Derived from the registry rather than
                # written as a number, so registering an event does not fail
                # this file.
                page_cap=len(ALL_EVENT_TYPES),
            ),
            ListEndpoint(
                "list_deliveries",
                lambda w: (
                    f"/api/v1/webhooks/endpoints/{_WEBHOOK_ENDPOINTS[w.org.id].id}/deliveries"
                ),
                _seed_webhook_deliveries,
            ),
        ]
    )


# --- measurement ------------------------------------------------------------


async def _measure_get(
    world: World, url: str, *, as_superadmin: bool = False
) -> tuple[QueryLog, Any]:
    # A superadmin needs a session that passed a second factor (review R3).
    token = (
        await session_token(world.session, world.superadmin)
        if as_superadmin
        else create_access_token(world.owner.id, None, ())
    )
    world.client.headers["Authorization"] = f"Bearer {token}"
    # Cold cache on both measurements: T-240 caches the user, the org row, the
    # org's entitlements, the feature-flag map and the invoice page, so a warm
    # Redis would make the two runs incomparable (and would hide an N+1 behind
    # a cache hit).
    await world.redis.flushdb()

    async with count_queries(world.session) as log:
        response = await world.client.get(url)

    assert response.status_code == 200, f"GET {url}: {response.status_code} {response.text}"
    return log, response.json()["data"]


async def _measure(world: World, endpoint: ListEndpoint) -> tuple[QueryLog, Any]:
    log, data = await _measure_get(world, endpoint.url(world), as_superadmin=endpoint.as_superadmin)
    return log, endpoint.items(data)


@pytest.mark.parametrize("endpoint", ENDPOINTS, ids=lambda e: e.name)
async def test_list_endpoint_issues_a_constant_number_of_queries(
    world: World, endpoint: ListEndpoint
) -> None:
    await endpoint.seed(world, _SEED_FIRST)
    small_log, small_items = await _measure(world, endpoint)

    await endpoint.seed(world, _SEED_SECOND)
    large_log, large_items = await _measure(world, endpoint)

    # The seed has to actually reach the response, or "constant" is vacuous.
    assert len(small_items) >= _SEED_FIRST
    assert len(large_items) > len(small_items) or len(large_items) == endpoint.page_cap

    assert large_log.count == small_log.count, (
        f"{endpoint.name} issues {small_log.count} queries for {len(small_items)} rows but "
        f"{large_log.count} for {len(large_items)} -- the extra statements scale with the page, "
        f"which is an N+1.\n\n"
        f"--- {len(small_items)} rows ---\n{small_log.render()}\n\n"
        f"--- {len(large_items)} rows ---\n{large_log.render()}"
    )
    assert large_log.count <= BUDGETS[endpoint.name], (
        f"{endpoint.name} is over its budget of {BUDGETS[endpoint.name]} "
        f"({large_log.count} statements):\n{large_log.render()}"
    )


# --- detail endpoints that embed a list -------------------------------------
#
# Not `list_` routes, so the meta-check below doesn't reach them -- but each
# renders a collection, which is where the same per-row lookup showed up while
# T-243's sweep was being wired. Same property, asserted the same way.


async def _seed_payment_failure_events(world: World, n: int) -> None:
    orgs = OrganizationRepository(world.session)
    for _ in range(n):
        org = await orgs.create(name="Failing", slug=f"fail-{uuid4().hex[:8]}")
        world.session.add(
            AuditEvent(
                action="billing.payment_failed",
                resource_type="subscription",
                resource_id=f"sub_{uuid4().hex[:8]}",
                org_id=org.id,
            )
        )
    await world.session.commit()


async def test_admin_user_detail_does_not_query_once_per_membership(world: World) -> None:
    """T-141's user detail lists every org the user belongs to. Resolving
    those org names used to be one get_by_id per membership."""
    url = f"/api/v1/admin/users/{world.owner.id}"

    await _seed_orgs_for_owner(world, _SEED_FIRST)
    small_log, small_detail = await _measure_get(world, url, as_superadmin=True)

    await _seed_orgs_for_owner(world, _SEED_SECOND)
    large_log, large_detail = await _measure_get(world, url, as_superadmin=True)

    assert len(large_detail["memberships"]) > len(small_detail["memberships"])
    assert large_log.count == small_log.count, (
        f"admin user detail issues {small_log.count} queries for "
        f"{len(small_detail['memberships'])} memberships but {large_log.count} for "
        f"{len(large_detail['memberships'])}:\n{large_log.render()}"
    )


async def test_admin_dashboard_does_not_query_once_per_payment_failure(world: World) -> None:
    """T-143's dashboard resolves an org name per recent payment failure. The
    feed is capped at 5, which made the old per-row lookup cheap -- but a cap
    is a smaller N, not the absence of one."""
    url = "/api/v1/admin/dashboard"

    await _seed_payment_failure_events(world, _SEED_FIRST)
    small_log, small_stats = await _measure_get(world, url, as_superadmin=True)

    await _seed_payment_failure_events(world, _SEED_SECOND)
    large_log, large_stats = await _measure_get(world, url, as_superadmin=True)

    assert len(large_stats["recent_payment_failures"]) > len(small_stats["recent_payment_failures"])
    assert large_log.count == small_log.count, (
        f"admin dashboard issues {small_log.count} queries for "
        f"{len(small_stats['recent_payment_failures'])} payment failures but "
        f"{large_log.count} for {len(large_stats['recent_payment_failures'])}:"
        f"\n{large_log.render()}"
    )


# --- the meta-check ---------------------------------------------------------


def _list_route_names(routes: list[BaseRoute]) -> set[str]:
    """Every GET route whose endpoint is named `list_...` -- FastAPI 0.137
    keeps included routers lazy (`_IncludedRouter`), so `app.routes` alone
    only sees the top level; `iter_route_contexts` is what flattens them (it
    is what `get_openapi` itself iterates)."""
    return {
        ctx.name
        for ctx in iter_route_contexts(routes)
        if "GET" in (ctx.methods or set()) and ctx.name.startswith("list_")
    }


def unbudgeted_list_routes(routes: list[BaseRoute]) -> set[str]:
    return _list_route_names(routes) - BUDGETS.keys()


def test_every_list_route_has_a_budget() -> None:
    missing = sorted(unbudgeted_list_routes(app.routes))
    assert not missing, (
        f"list endpoints with no query budget: {missing}. Add each to BUDGETS and to ENDPOINTS "
        "with a seed, so an N+1 on it fails the day it is written."
    )


def test_meta_check_catches_a_new_unbudgeted_list_route() -> None:
    """The guard's own guard: a route added tomorrow really does trip it."""
    router = APIRouter()

    @router.get("/widgets")
    async def list_widgets() -> list[str]:
        return []

    throwaway = FastAPI()
    throwaway.include_router(router)

    assert unbudgeted_list_routes(throwaway.routes) == {"list_widgets"}


def test_route_traversal_still_finds_the_real_routes() -> None:
    """If a FastAPI upgrade changes how included routers are flattened,
    _list_route_names could quietly return nothing -- and then
    test_every_list_route_has_a_budget would pass while guarding nothing."""
    assert _list_route_names(app.routes) >= {"list_members", "list_notifications", "list_files"}


def test_every_budget_has_a_covering_endpoint_spec() -> None:
    assert {e.name for e in ENDPOINTS} == BUDGETS.keys()
