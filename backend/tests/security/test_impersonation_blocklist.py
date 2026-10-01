"""
T-260: during an impersonation session the server refuses anything that would
produce a side effect the real user never asked for -- mail they didn't
request, a charge they didn't authorise, a password they didn't change, an
unread badge they didn't clear.

Three layers, in the order a regression would show up:

1. **The sweep** (`test_every_mutating_route_is_decided`). Every POST/PUT/
   PATCH/DELETE route in the app either carries
   `Depends(reject_during_impersonation(...))` or an explicit
   `# impersonation-ok: <reason>` comment in its endpoint body. A route added
   tomorrow with neither fails the build the day it is written -- the same
   pattern as T-243's query budgets and T-049's cross-tenant sweep. The marker
   is a comment rather than a list inside this file for the same reason T-252
   put the runbook exemption in the runbook: the reason belongs where the
   reader is.

2. **The live matrix** (`test_guarded_route_blocks_an_impersonation_session`).
   Every guarded route is *actually hit* with a real impersonation token and
   must answer 403 `errors.impersonation_blocked`, carrying the category the
   route table claims. The cases are generated from the route table, so this
   half can't drift from layer 1: adding a guard adds a case.

3. **The named flows**, with their side effects checked rather than just the
   status code -- the notification stays unread, no email job is enqueued --
   plus the audit trail every block leaves, and the push suppression that
   covers what the blocklist can't (a notification raised by something *else*
   while an agent is impersonating the recipient).

Requests in the matrix are deliberately sent with empty bodies and throwaway
path parameters: `reject_during_impersonation` is a route-level dependency, so
FastAPI solves it before it validates the body, and a 403 rather than a 422
is itself the proof that the block lands ahead of everything else on the
route -- including the permission and step-up gates.
"""

import inspect
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
import pytest
import stripe
from fastapi import APIRouter, Depends, FastAPI
from fastapi.routing import iter_route_contexts
from httpx import ASGITransport
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.routing import BaseRoute

from app.core.dependencies import blocked_action_kind, reject_during_impersonation
from app.main import app
from app.middleware.impersonation import ImpersonationContextMiddleware
from app.modules.audit.models import AuditEvent
from app.modules.auth.models import User
from app.modules.auth.permissions import MemberRole
from app.modules.auth.repository import UserRepository
from app.modules.auth.utils import create_access_token, hash_password
from app.modules.billing.models import Plan
from app.modules.notifications.models import Notification
from app.modules.notifications.repository import NotificationRepository
from app.modules.notifications.service import NotificationService
from app.modules.tenancy.models import Organization
from app.modules.tenancy.repository import MembershipRepository, OrganizationRepository

pytestmark = pytest.mark.owasp("A01")

_PASSWORD = "Correct-Horse9"
_MARKER = "impersonation-ok:"
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


# --- route table introspection ----------------------------------------------


@dataclass(frozen=True)
class RouteFacts:
    method: str
    path: str
    name: str
    kind: str | None
    marked: bool

    @property
    def label(self) -> str:
        return f"{self.method} {self.path}"


def _guard_kinds(dependant: Any) -> list[str]:  # noqa: ANN401 -- fastapi.dependencies.models.Dependant is not exported
    """Every T-260 category reachable from this route's dependency tree.

    Recursive rather than a scan of the top level: a guard could just as
    legitimately be attached to a shared sub-dependency, and a coverage check
    that only looked one level deep would report that route as uncovered.
    """
    found: list[str] = []
    for dependency in dependant.dependencies:
        kind = blocked_action_kind(dependency.call)
        if kind is not None:
            found.append(kind)
        found.extend(_guard_kinds(dependency))
    return found


def _has_marker(endpoint: Any) -> bool:  # noqa: ANN401 -- an arbitrary endpoint callable
    try:
        # getsource unwraps functools.wraps, so this is the endpoint's own
        # source rather than @api_limit's wrapper (see test_the_marker_is_read
        # _through_the_rate_limit_decorator below, which pins that).
        source = inspect.getsource(endpoint)
    except (OSError, TypeError):  # pragma: no cover -- every route here is a def in a file
        return False
    return _MARKER in source


def route_facts(routes: list[BaseRoute]) -> list[RouteFacts]:
    """Flattened (method, path, guard-or-marker) for every route in `routes`.

    FastAPI 0.137 keeps included routers lazy (`_IncludedRouter`), so
    `app.routes` alone only sees the top level -- `iter_route_contexts` is what
    flattens them, and is what `get_openapi` itself iterates.
    """
    facts: list[RouteFacts] = []
    for ctx in iter_route_contexts(routes):
        dependant = getattr(ctx, "dependant", None)
        if dependant is None or ctx.path is None or ctx.endpoint is None:
            continue
        kinds = _guard_kinds(dependant)
        assert len(set(kinds)) <= 1, f"{ctx.path} carries conflicting guards: {kinds}"
        facts.extend(
            RouteFacts(
                method=method,
                path=ctx.path,
                name=ctx.name or "",
                kind=kinds[0] if kinds else None,
                marked=_has_marker(ctx.endpoint),
            )
            for method in sorted(ctx.methods or set())
        )
    return facts


def undecided_routes(routes: list[BaseRoute]) -> list[str]:
    return sorted(
        fact.label
        for fact in route_facts(routes)
        if fact.method in _MUTATING_METHODS and fact.kind is None and not fact.marked
    )


_APP_ROUTES = route_facts(app.routes)
_GUARDED = [fact for fact in _APP_ROUTES if fact.kind is not None]


def test_every_mutating_route_is_decided() -> None:
    missing = undecided_routes(app.routes)
    assert not missing, (
        f"routes with no impersonation decision: {missing}. Add "
        "dependencies=[Depends(reject_during_impersonation(<kind>))] to the route, or an "
        "explicit `# impersonation-ok: <reason>` comment in the endpoint saying why acting as "
        "the impersonated user here is harmless."
    )


def test_meta_check_catches_a_new_undecided_route() -> None:
    """The guard's own guard: a write route added tomorrow really does trip it."""
    router = APIRouter()

    @router.post("/widgets")
    async def create_widget() -> dict[str, str]:
        return {}

    @router.get("/widgets")
    async def list_widgets() -> list[str]:
        return []

    throwaway = FastAPI()
    throwaway.include_router(router)

    # The GET is not in the list: reads produce no side effect to block.
    assert undecided_routes(throwaway.routes) == ["POST /widgets"]


def test_meta_check_accepts_either_a_guard_or_a_marker() -> None:
    router = APIRouter()

    @router.post("/guarded", dependencies=[Depends(reject_during_impersonation("billing"))])
    async def guarded() -> dict[str, str]:
        return {}

    @router.post("/marked")
    async def marked() -> dict[str, str]:
        # impersonation-ok: nothing happens here at all.
        return {}

    throwaway = FastAPI()
    throwaway.include_router(router)

    assert undecided_routes(throwaway.routes) == []


def test_the_marker_is_read_through_the_rate_limit_decorator() -> None:
    """Every real route is wrapped by @api_limit/@auth_limit/@sensitive_limit.
    If a slowapi upgrade ever dropped functools.wraps, `inspect.getsource`
    would return the wrapper's body, no marker would ever be found, and this
    file would start demanding guards on /auth/login."""
    login = next(fact for fact in _APP_ROUTES if fact.path == "/api/v1/auth/login")

    assert login.marked


# T-291: the eight tests marked in this file name a specific module's route
# (billing checkout, notification read-state) or assert over the whole guarded
# set, which shrinks when a module is off. The sweep itself -- every write
# route carries a guard or an `# impersonation-ok:` comment -- is core and
# runs in every combination of CI's module-matrix job.
@pytest.mark.module_dependent
def test_route_traversal_still_finds_the_real_routes() -> None:
    """If a FastAPI upgrade changes how included routers are flattened,
    route_facts could quietly return nothing -- and then the sweep above would
    pass while guarding an empty set."""
    guarded_paths = {fact.path for fact in _GUARDED}

    assert {
        "/api/v1/auth/forgot-password",
        "/api/v1/billing/checkout-session",
        "/api/v1/notifications/{notification_id}/read",
    } <= guarded_paths


def test_guarding_a_public_route_does_not_make_it_look_authenticated() -> None:
    """The guard reads the Authorization header itself rather than depending on
    `oauth2_scheme`, precisely so it can be added to an unauthenticated route
    without changing that route's contract. Declared through the dependency
    system instead, every generated client would start believing
    /auth/forgot-password needs a bearer token."""
    schema = app.openapi()

    assert schema["paths"]["/api/v1/auth/forgot-password"]["post"].get("security") is None
    assert schema["paths"]["/api/v1/auth/magic-link"]["post"].get("security") is None
    # The contrast case: an endpoint that genuinely requires a user still says so.
    assert schema["paths"]["/api/v1/auth/change-password"]["post"]["security"] == [
        {"OAuth2PasswordBearer": []}
    ]


@pytest.mark.module_dependent
def test_every_category_is_actually_used() -> None:
    """A category nobody applies is a category that was renamed in one place
    and not the other -- BlockedActionKind is a closed set for exactly that
    reason, so every member must be reachable from a real route."""
    assert {fact.kind for fact in _GUARDED} == {
        "notifying",
        "billing",
        "data_mutation",
        "notification_read_state",
    }


# --- the world every live test below shares ---------------------------------


@dataclass
class ImpersonatedWorld:
    client: httpx.AsyncClient
    session: AsyncSession
    user: User
    superadmin: User
    org: Organization
    impersonation_headers: dict[str, str]
    own_headers: dict[str, str]


async def _make_user(session: AsyncSession, *, is_superadmin: bool = False) -> User:
    return await UserRepository(session).create(
        email=f"user-{uuid4()}@example.com",
        hashed_password=hash_password(_PASSWORD),
        email_verified_at=datetime.now(UTC),
        is_superadmin=is_superadmin,
    )


@pytest.fixture
async def world(client: httpx.AsyncClient, session: AsyncSession) -> ImpersonatedWorld:
    user = await _make_user(session)
    superadmin = await _make_user(session, is_superadmin=True)
    org = await OrganizationRepository(session).create(name="Acme", slug=f"acme-{uuid4()}")
    await MembershipRepository(session).create(
        user_id=user.id, org_id=org.id, role=MemberRole.OWNER
    )
    await session.commit()

    # Exactly what POST /admin/users/{id}/impersonate mints (T-142): a token
    # for `user`, carrying the acting superadmin in impersonator_id.
    impersonation_token = create_access_token(user.id, None, (), impersonator_id=superadmin.id)
    own_token = create_access_token(user.id, None, ())
    return ImpersonatedWorld(
        client=client,
        session=session,
        user=user,
        superadmin=superadmin,
        org=org,
        impersonation_headers={
            "Authorization": f"Bearer {impersonation_token}",
            "X-Org-ID": str(org.id),
        },
        own_headers={"Authorization": f"Bearer {own_token}", "X-Org-ID": str(org.id)},
    )


def _concrete_path(path: str) -> str:
    """A real URL for a templated path. The values are throwaway on purpose --
    the block has to land before anything resolves them."""

    def _substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        return str(uuid4()) if name.endswith("_id") else "placeholder"

    return re.sub(r"\{([^}]+)\}", _substitute, path)


@pytest.mark.parametrize("fact", _GUARDED, ids=[f"{fact.kind}: {fact.label}" for fact in _GUARDED])
async def test_guarded_route_blocks_an_impersonation_session(
    fact: RouteFacts, world: ImpersonatedWorld
) -> None:
    response = await world.client.request(
        fact.method,
        _concrete_path(fact.path),
        json={},
        headers=world.impersonation_headers,
    )

    assert response.status_code == 403, response.text
    body = response.json()
    assert body["error"]["message_key"] == "errors.impersonation_blocked"
    assert body["error"]["details"] == {"kind": fact.kind}
    assert body["data"] is None


async def test_an_ordinary_session_is_not_blocked_by_the_guard(world: ImpersonatedWorld) -> None:
    """The same request, the same user, the same throwaway path parameter --
    only the impersonator_id claim differs. It must get past the guard and be
    judged on its own merits (404 for the made-up session id)."""
    response = await world.client.delete(
        f"/api/v1/auth/sessions/{uuid4()}", headers=world.own_headers
    )

    assert response.status_code == 404
    assert response.json()["error"]["message_key"] != "errors.impersonation_blocked"


# --- the named flows, with their side effects -------------------------------


async def test_forgot_password_during_impersonation_is_blocked_and_sends_nothing(
    world: ImpersonatedWorld, enqueued_jobs: list[tuple[str, dict[str, Any]]]
) -> None:
    response = await world.client.post(
        "/api/v1/auth/forgot-password",
        json={"email": world.user.email},
        headers=world.impersonation_headers,
    )

    assert response.status_code == 403
    assert enqueued_jobs == []


async def test_forgot_password_without_impersonation_still_works(
    world: ImpersonatedWorld, enqueued_jobs: list[tuple[str, dict[str, Any]]]
) -> None:
    # Unauthenticated, like the real flow: the guard must not have turned a
    # public endpoint into one that needs a token.
    response = await world.client.post(
        "/api/v1/auth/forgot-password", json={"email": world.user.email}
    )

    assert response.status_code == 200
    assert [function for function, _ in enqueued_jobs] == ["send_email"]


async def test_a_blocked_attempt_writes_an_elevated_audit_event(
    world: ImpersonatedWorld,
) -> None:
    # Read now: the 403 rolls back the session this test shares with the
    # app, which expires every row the world holds.
    user_id, superadmin_id = world.user.id, world.superadmin.id
    await world.client.post(
        "/api/v1/auth/forgot-password",
        json={"email": world.user.email},
        headers=world.impersonation_headers,
    )

    # Committed by the guard itself: the 403 unwinds get_session's rollback,
    # so an uncommitted event would be gone by the time the response is
    # written -- the failure mode this assertion exists to catch.
    events = (
        (
            await world.session.execute(
                select(AuditEvent).where(AuditEvent.action == "impersonation.blocked")
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    event = events[0]
    assert event.severity == "elevated"
    assert event.actor_id == user_id
    assert event.impersonator_id == superadmin_id
    assert event.diff == {
        "kind": "notifying",
        "method": "POST",
        "path": "/api/v1/auth/forgot-password",
    }


@pytest.mark.module_dependent
async def test_checkout_session_during_impersonation_is_blocked(
    world: ImpersonatedWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = Plan(
        name="Pro",
        slug=f"pro-{uuid4().hex[:8]}",
        stripe_price_id_monthly="price_monthly_1",
        stripe_price_id_yearly="price_yearly_1",
        price_monthly=2900,
        price_yearly=29000,
        features={"seats": 5},
    )
    world.session.add(plan)
    await world.session.commit()

    async def _customer_create(**_kwargs: object) -> object:
        raise AssertionError("Stripe must never be reached during impersonation")

    monkeypatch.setattr(stripe.Customer, "create_async", _customer_create)

    response = await world.client.post(
        "/api/v1/billing/checkout-session",
        json={"plan_slug": plan.slug, "interval": "monthly"},
        headers=world.impersonation_headers,
    )

    assert response.status_code == 403
    assert response.json()["error"]["details"] == {"kind": "billing"}


@pytest.mark.module_dependent
async def test_marking_a_notification_read_during_impersonation_leaves_it_unread(
    world: ImpersonatedWorld, redis: Redis
) -> None:
    repo = NotificationRepository(world.session, world.org.id, redis)
    notification = await repo.create(
        user_id=world.user.id,
        type="weekly_summary",
        title="Your week",
        body="Here is your week.",
        params={"org_name": "Acme"},
        action_url=None,
        channels=["in_app"],
        idempotency_key=None,
    )
    await world.session.commit()
    notification_id = notification.id  # read before the 403 expires it

    response = await world.client.patch(
        f"/api/v1/notifications/{notification_id}/read", headers=world.impersonation_headers
    )

    assert response.status_code == 403
    assert response.json()["error"]["details"] == {"kind": "notification_read_state"}
    still_unread = await world.session.get(Notification, notification_id)
    assert still_unread is not None
    await world.session.refresh(still_unread)
    assert still_unread.read_at is None


@pytest.mark.module_dependent
async def test_the_same_user_can_mark_it_read_themselves(
    world: ImpersonatedWorld, redis: Redis
) -> None:
    repo = NotificationRepository(world.session, world.org.id, redis)
    notification = await repo.create(
        user_id=world.user.id,
        type="weekly_summary",
        title="Your week",
        body="Here is your week.",
        params={"org_name": "Acme"},
        action_url=None,
        channels=["in_app"],
        idempotency_key=None,
    )
    await world.session.commit()

    response = await world.client.patch(
        f"/api/v1/notifications/{notification.id}/read", headers=world.own_headers
    )

    assert response.status_code == 200
    assert response.json()["data"]["read_at"] is not None


# --- push suppression (the half a route blocklist can't cover) --------------


@pytest.fixture
def push_probe(world: ImpersonatedWorld, redis: Redis) -> httpx.AsyncClient:
    """A one-route app behind the *real* ImpersonationContextMiddleware.

    The blocklist above means no reachable endpoint both survives an
    impersonation session and sends a notification -- which is the point, but
    it leaves nowhere to prove the suppression end to end through a real
    request. This probe is that missing endpoint: the middleware, the
    ContextVar and NotificationService.send() are all the production ones, and
    only the route calling them is synthetic.
    """
    probe = FastAPI()
    probe.add_middleware(ImpersonationContextMiddleware)

    @probe.post("/probe")
    async def _send_payment_failed() -> dict[str, str]:
        await NotificationService(world.session, redis).send(
            user_id=world.user.id,
            org_id=world.org.id,
            type="payment_failed",
            params={"org_name": "Acme", "amount": "$49.00"},
        )
        await world.session.commit()
        return {"sent": "1"}

    return httpx.AsyncClient(transport=ASGITransport(app=probe), base_url="https://testserver")


@pytest.mark.module_dependent
async def test_a_notification_raised_during_impersonation_sends_no_push(
    world: ImpersonatedWorld,
    push_probe: httpx.AsyncClient,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    async with push_probe as probe:
        response = await probe.post("/probe", headers=world.impersonation_headers)

    assert response.status_code == 200
    # payment_failed defaults to email + in_app + push; the agent's activity
    # must not buzz the phone in the real user's pocket.
    assert [function for function, _ in enqueued_jobs] == ["send_email"]


@pytest.mark.module_dependent
async def test_the_same_notification_outside_impersonation_does_send_a_push(
    world: ImpersonatedWorld,
    push_probe: httpx.AsyncClient,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    async with push_probe as probe:
        response = await probe.post("/probe", headers=world.own_headers)

    assert response.status_code == 200
    assert {function for function, _ in enqueued_jobs} == {"send_email", "send_push"}


@pytest.mark.module_dependent
async def test_the_suppressed_push_is_recorded_on_the_audit_event(
    world: ImpersonatedWorld, push_probe: httpx.AsyncClient
) -> None:
    async with push_probe as probe:
        await probe.post("/probe", headers=world.impersonation_headers)

    events = (
        (
            await world.session.execute(
                select(AuditEvent).where(AuditEvent.action == "notification.sent")
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].diff is not None
    assert events[0].diff["push_suppressed"] is True
    assert events[0].impersonator_id == world.superadmin.id


async def test_a_forged_impersonator_header_does_not_block_an_ordinary_session(
    world: ImpersonatedWorld,
) -> None:
    """The claim is read off the signed JWT, never off anything a client can
    set -- so a header naming an impersonator changes nothing."""
    response = await world.client.post(
        "/api/v1/auth/forgot-password",
        json={"email": world.user.email},
        headers={**world.own_headers, "X-Impersonator-Id": str(world.superadmin.id)},
    )

    assert response.status_code == 200
