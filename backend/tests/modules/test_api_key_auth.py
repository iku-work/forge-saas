"""
Authenticating as a key rather than as a person (T-311).

Run against the **real** `app.main` app, not a throwaway one, and that is
possible because of the design rather than in spite of it: the two endpoints a
key can call belong to `tenancy` and are always registered. A key is a second
way to authenticate against endpoints the product already serves, not a
parallel API, so there is nothing module-gated in the routing table to stand
up here.

What *is* module-gated is the auth path itself: `_principal_from_key` refuses
outright unless `public_api` is enabled, because `api_key` rows survive the
toggle being turned off (that is what MODULE_TABLES is for) and a stale row in
a table that still physically exists must not authenticate. The
`public_api_enabled` fixture below flips that toggle for the tests that need a
key to work, and `test_a_key_stops_working_when_the_module_is_turned_off`
mints a key with it on and then turns it off mid-test, which is the sequence a
product that changes its mind actually performs.

The `api_key` table has to be created here for the same reason it does in
test_api_keys.py: `migration_targets()` skips the `public_api` branch while
the module is off, so the shared `engine` fixture never creates it.
"""

from collections.abc import Generator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.routing import iter_route_contexts
from limits.storage import RedisStorage
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlmodel import SQLModel

from app.core import module_loader
from app.core.config import get_settings
from app.core.modules import ModuleConfig
from app.core.principal import declared_scope
from app.main import app
from app.models._mixins import utcnow
from app.modules.api_keys.models import KEY_PREFIX, ApiKey
from app.modules.api_keys.schemas import ApiKeyCreateRequest
from app.modules.api_keys.service import ApiKeyService
from app.modules.auth.permissions import MemberRole
from app.modules.auth.repository import UserRepository
from app.modules.auth.utils import create_access_token, hash_password
from app.modules.tenancy.models import Organization
from app.modules.tenancy.repository import MembershipRepository, OrganizationRepository
from product import config as product_config

pytestmark = pytest.mark.owasp("A01")

_PASSWORD = "Correct-Horse9"

# Raw SQL rather than the repository, in both cases deliberately: these two
# statements are about what the *database* holds, and going through the layer
# under test would let a bug in it hide itself.
_EXPIRE_ALL = text("UPDATE api_key SET expires_at = now() - interval '1 hour'")
_LAST_USED = text("SELECT last_used_at FROM api_key")


@pytest.fixture(autouse=True)
async def _api_key_table(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all, tables=[ApiKey.__table__])


@pytest.fixture
def public_api_enabled(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """Turn the module on for one test.

    Monkeypatches `product.config.MODULES` and clears `get_modules`'s cache,
    which is the same thing tests/modules/test_module_toggles.py does and the
    only thing this needs: nothing about *routing* changes, because the routes
    a key calls are always registered. `is_enabled("public_api")` is what the
    auth path reads, and this is what makes it true.
    """
    monkeypatch.setattr(
        product_config,
        "MODULES",
        replace(product_config.MODULES, public_api=ModuleConfig(enabled=True)),
    )
    module_loader.get_modules.cache_clear()
    yield
    module_loader.get_modules.cache_clear()


@dataclass
class KeyWorld:
    client: httpx.AsyncClient
    session: AsyncSession
    redis: Redis
    org: Organization
    other_org: Organization
    owner_id: UUID
    owner_token: str


async def _org_with_owner(session: AsyncSession) -> tuple[Organization, UUID]:
    user = await UserRepository(session).create(
        email=f"user-{uuid4()}@example.com",
        hashed_password=hash_password(_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    org = await OrganizationRepository(session).create(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    await MembershipRepository(session).create(
        user_id=user.id, org_id=org.id, role=MemberRole.OWNER
    )
    return org, user.id


@pytest.fixture
async def world(
    client: httpx.AsyncClient,
    session: AsyncSession,
    redis: Redis,
    rate_limit_storage: RedisStorage,
) -> KeyWorld:
    # T-312 keys a limiter on the key's id, and `.env.test` sets that budget
    # to 5. Without a reset, a fresh key inherits nothing -- but the *user*
    # limiter behind @api_limit is keyed on the client address, and a file
    # this size would exhaust it. Same reset the other api-key suites do.
    rate_limit_storage.reset()
    org, owner_id = await _org_with_owner(session)
    other_org, _ = await _org_with_owner(session)
    await session.commit()
    return KeyWorld(
        client=client,
        session=session,
        redis=redis,
        org=org,
        other_org=other_org,
        owner_id=owner_id,
        owner_token=create_access_token(owner_id, None, ()),
    )


async def _mint(
    world: KeyWorld,
    *,
    scopes: list[str],
    org: Organization | None = None,
    expires_at: datetime | None = None,
) -> str:
    service = ApiKeyService(world.session, (org or world.org).id, world.redis)
    _, presented = await service.create_key(
        world.owner_id,
        ApiKeyCreateRequest(name="integration", scopes=scopes, expires_at=expires_at),
    )
    await world.session.commit()
    return presented


def _members_url(world: KeyWorld) -> str:
    return f"/api/v1/orgs/{world.org.id}/members"


def _key_headers(world: KeyWorld, presented: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {presented}", "X-Org-ID": str(world.org.id)}


# --- the happy path and its three refusals -----------------------------------


@pytest.mark.usefixtures("public_api_enabled")
async def test_a_valid_key_with_the_right_scope_is_served(world: KeyWorld) -> None:
    presented = await _mint(world, scopes=["members:read"])

    response = await world.client.get(_members_url(world), headers=_key_headers(world, presented))

    assert response.status_code == 200
    assert len(response.json()["data"]) == 1


@pytest.mark.usefixtures("public_api_enabled")
async def test_a_key_without_the_scope_is_refused(world: KeyWorld) -> None:
    """403, not 401: the credential is real and the caller should be told the
    problem is what it may do, not who it is."""
    presented = await _mint(world, scopes=["orgs:read"])

    response = await world.client.get(_members_url(world), headers=_key_headers(world, presented))

    assert response.status_code == 403
    assert response.json()["error"]["message_key"] == "errors.missing_scope"


@pytest.mark.usefixtures("public_api_enabled")
async def test_an_unknown_key_is_refused(world: KeyWorld) -> None:
    fabricated = f"{KEY_PREFIX}{'a' * 12}_{'b' * 43}"

    response = await world.client.get(_members_url(world), headers=_key_headers(world, fabricated))

    assert response.status_code == 401


@pytest.mark.usefixtures("public_api_enabled")
@pytest.mark.parametrize(
    "mangle",
    [
        pytest.param(lambda k: k[:-1] + ("x" if k[-1] != "x" else "y"), id="one character off"),
        pytest.param(lambda k: k + "extra", id="trailing junk"),
        pytest.param(lambda k: f"{KEY_PREFIX}{k.split('_')[-1]}", id="no prefix segment"),
        pytest.param(
            lambda k: k.replace(KEY_PREFIX, f"{KEY_PREFIX}z", 1),
            id="a prefix of the wrong length",
        ),
    ],
)
async def test_every_wrong_key_gets_the_same_answer(world: KeyWorld, mangle: object) -> None:
    """Malformed, unknown and wrong-secret are one answer with one message.

    Telling a caller "that prefix exists but the secret is wrong" would turn
    the prefix -- which is not a secret, and is printed in the management UI
    -- into an oracle for which of them are real. Every case here carries the
    product's own `fk_live_` prefix, so every one of them reaches the api-key
    path and is answered by it.
    """
    presented = await _mint(world, scopes=["members:read"])
    broken = mangle(presented)  # type: ignore[operator]

    response = await world.client.get(_members_url(world), headers=_key_headers(world, broken))

    assert response.status_code == 401
    assert response.json()["error"]["message_key"] == "errors.invalid_api_key"


@pytest.mark.usefixtures("public_api_enabled")
@pytest.mark.parametrize(
    "token",
    [
        pytest.param("sk_live_abcdef123456_secret", id="another product's prefix"),
        pytest.param("not-a-token-at-all", id="not a token"),
        pytest.param("", id="empty"),
    ],
)
async def test_a_token_that_is_not_an_api_key_never_reaches_the_api_key_path(
    world: KeyWorld, token: str
) -> None:
    """The dispatch is on the prefix, and only on the prefix.

    A bearer token that does not start with `fk_live_` is a JWT as far as
    this codebase is concerned, and is rejected by `verify_access_token` with
    the message that path has always used. Asserting the *different* message
    is the point: it is the evidence that nothing about the JWT path changed
    for tokens that were never API keys.
    """
    response = await world.client.get(
        _members_url(world),
        headers={"Authorization": f"Bearer {token}", "X-Org-ID": str(world.org.id)},
    )

    assert response.status_code == 401
    assert response.json()["error"]["message_key"] != "errors.invalid_api_key"


@pytest.mark.usefixtures("public_api_enabled")
async def test_an_expired_key_is_refused(world: KeyWorld) -> None:
    presented = await _mint(
        world, scopes=["members:read"], expires_at=utcnow() + timedelta(seconds=1)
    )
    assert (
        await world.client.get(_members_url(world), headers=_key_headers(world, presented))
    ).status_code == 200

    # Expire it in the database rather than sleeping: what is being tested is
    # the comparison, not the clock.
    await world.session.execute(_EXPIRE_ALL)
    await world.session.commit()
    await world.redis.flushdb()  # the row is cached for a minute (T-240)

    response = await world.client.get(_members_url(world), headers=_key_headers(world, presented))
    assert response.status_code == 401


@pytest.mark.usefixtures("public_api_enabled")
async def test_a_revoked_key_stops_working_within_the_same_request(world: KeyWorld) -> None:
    """T-310's cache requirement, from the outside. The lookup is cached for
    a minute, so a revocation that did not bust the entry would leave the key
    working for the rest of it -- which is the whole failure mode revocation
    exists to prevent."""
    presented = await _mint(world, scopes=["members:read"])
    headers = _key_headers(world, presented)
    assert (await world.client.get(_members_url(world), headers=headers)).status_code == 200

    keys = await ApiKeyService(world.session, world.org.id, world.redis).list_keys(None, 10)
    await ApiKeyService(world.session, world.org.id, world.redis).revoke_key(
        world.owner_id, keys[0][0].id
    )
    await world.session.commit()

    # No flushdb: the point is that revoke_key busted the entry itself.
    assert (await world.client.get(_members_url(world), headers=headers)).status_code == 401


@pytest.mark.usefixtures("public_api_enabled")
async def test_a_key_cannot_reach_another_orgs_data(world: KeyWorld) -> None:
    """§0.2, for the second kind of principal. The key belongs to `other_org`;
    the request addresses `org`."""
    presented = await _mint(world, scopes=["members:read"], org=world.other_org)

    response = await world.client.get(_members_url(world), headers=_key_headers(world, presented))

    assert response.status_code == 403
    assert response.json()["error"]["message_key"] == "errors.not_org_member"


# --- the JWT path is untouched -----------------------------------------------


async def test_a_malformed_org_id_in_the_path_is_a_422_about_the_path(world: KeyWorld) -> None:
    """A dependency is solved before the route's own `org_id: UUID` parameter
    is validated, so `require_scope` sees the raw string first. Without a
    deliberate answer it fell through to "X-Org-ID header is required", which
    is a 400 about a header the caller never needed to send."""
    response = await world.client.get(
        "/api/v1/orgs/not-a-uuid/members",
        headers={"Authorization": f"Bearer {world.owner_token}", "X-Org-ID": str(world.org.id)},
    )

    assert response.status_code == 422


async def test_a_jwt_still_authenticates_on_the_same_header(world: KeyWorld) -> None:
    """No fixture: this has to hold whether or not the module is enabled,
    because the JWT path never had anything to do with it."""
    response = await world.client.get(
        _members_url(world),
        headers={"Authorization": f"Bearer {world.owner_token}", "X-Org-ID": str(world.org.id)},
    )

    assert response.status_code == 200


async def test_a_session_still_needs_its_permission_on_an_opened_route(
    world: KeyWorld, session: AsyncSession
) -> None:
    """Opening a route to keys must not widen what a *user* may do. A member
    of another organization is refused exactly as before."""
    outsider, outsider_id = await _org_with_owner(session)
    await session.commit()

    response = await world.client.get(
        _members_url(world),
        headers={
            "Authorization": f"Bearer {create_access_token(outsider_id, None, ())}",
            "X-Org-ID": str(outsider.id),
        },
    )

    assert response.status_code == 403


# --- opt-in, per endpoint ----------------------------------------------------


@pytest.mark.usefixtures("public_api_enabled")
@pytest.mark.parametrize(
    "url",
    [
        pytest.param("/api/v1/auth/me", id="a user-only route"),
        pytest.param("/api/v1/orgs", id="the org list"),
        # `gdpr` off means this route does not exist, and a 404 proves
        # nothing about how a key is treated -- the two routes above are
        # always registered and carry the assertion on their own.
        pytest.param(
            "/api/v1/me/consents",
            id="a gdpr route",
            marks=pytest.mark.module_dependent,
        ),
    ],
)
async def test_a_route_without_require_scope_rejects_a_valid_key(world: KeyWorld, url: str) -> None:
    """The default for every existing and future endpoint. `get_current_user`
    tries to verify the key as a JWT, fails, and 401s -- which is exactly
    right: it is not a token this route accepts."""
    presented = await _mint(world, scopes=["members:read", "orgs:read", "files:read"])

    response = await world.client.get(url, headers=_key_headers(world, presented))

    assert response.status_code == 401


@pytest.mark.usefixtures("public_api_enabled")
async def test_a_key_cannot_manage_keys(world: KeyWorld) -> None:
    """A key that could mint another key is a privilege-escalation primitive.
    Nothing under /api/v1/api-keys carries `require_scope`, so the management
    surface is unreachable with one -- and stays that way even in a product
    where the module is on and the routes are registered."""
    presented = await _mint(world, scopes=["members:read"])

    response = await world.client.post(
        "/api/v1/api-keys",
        headers=_key_headers(world, presented),
        json={"name": "escalated", "scopes": ["members:write"]},
    )

    # 404 at the committed configuration (the router is not registered), 401
    # in a product that enables the module (the route wants a session and a
    # key is not one). Never 200, and `test_the_public_surface_is_exactly_the
    # _routes_that_declare_a_scope` below is the version of this claim that
    # does not depend on which configuration is running.
    assert response.status_code in (401, 404)


@pytest.mark.usefixtures("public_api_enabled")
@pytest.mark.module_dependent
async def test_an_impersonation_endpoint_rejects_a_key_regardless_of_scope(
    world: KeyWorld,
) -> None:
    """T-260's blocklist and this task have to agree: a support agent's
    impersonation surface is superadmin-gated and session-only, and no scope
    opens it.

    Marked: the route lives in `admin`, so with that module off there is
    nothing to reject and the 404 would be measuring the toggle instead.
    """
    presented = await _mint(world, scopes=["members:read", "orgs:read", "files:read"])

    response = await world.client.post(
        f"/api/v1/admin/users/{world.owner_id}/impersonate",
        headers=_key_headers(world, presented),
        json={"reason": "because"},
    )

    assert response.status_code == 401


# --- the public surface, enumerated -----------------------------------------


def _scopes_in(dependant: Any) -> list[str]:  # noqa: ANN401 -- fastapi.dependencies.models.Dependant is not exported
    """Every scope reachable from a route's dependency tree.

    Recursive, for the same reason T-260's `_guard_kinds` is: a gate attached
    to a shared sub-dependency is still a gate, and a sweep that looked only
    at the top level would report the route as closed.
    """
    if dependant is None:
        return []
    found: list[str] = []
    for dependency in dependant.dependencies:
        scope = declared_scope(dependency.call)
        if scope is not None:
            found.append(scope)
        found.extend(_scopes_in(dependency))
    return found


def test_the_public_surface_is_exactly_the_routes_that_declare_a_scope() -> None:
    """T-311's "every key-callable endpoint is enumerable from the code".

    `declared_scope` walks a route's dependency tree the same way T-260's
    sweep walks it for `blocked_action_kind`, so this is the whole surface
    rather than a list somebody maintains. It is pinned here because widening
    it is a decision, not a refactor -- a new entry appearing in this diff is
    the review conversation the task asks for, and the docs table in
    docs/runbooks/public-api.md has to move with it.
    """
    surface: dict[str, str] = {}
    for ctx in iter_route_contexts(app.routes):
        scopes = _scopes_in(getattr(ctx, "dependant", None))
        assert len(set(scopes)) <= 1, f"{ctx.path} declares conflicting scopes: {scopes}"
        for method in sorted(ctx.methods or set()) if scopes else ():
            surface[f"{method} {ctx.path}"] = scopes[0]

    assert surface == {
        "GET /api/v1/orgs/{org_id}": "orgs:read",
        "GET /api/v1/orgs/{org_id}/members": "members:read",
    }


def test_no_key_callable_endpoint_writes_anything_yet() -> None:
    """The audit requirement T-311 states, enforced as an absence.

    Every key-authenticated *write* has to log `actor_type=api_key` and the
    key's id (never the key). Today there is nothing to log: the public
    surface is two reads, so the requirement is satisfied vacuously -- and a
    vacuous guarantee is worth exactly nothing unless something notices when
    it stops being vacuous.

    That is this test. The day a `require_scope` lands on a POST, PATCH,
    PUT or DELETE it goes red, and the message says what has to happen
    first: `AuditEvent` needs an `actor_type` column (a core migration, so
    `CORE_HEAD_REVISION` moves with it), `AuditService.log` needs to read it
    the way it already reads `impersonator_id` from
    `app.core.audit_context`, and `CurrentPrincipal.audit_fields()` -- which
    exists for this and is otherwise unused -- is what supplies the value.
    """
    writes = {
        f"{method} {ctx.path}"
        for ctx in iter_route_contexts(app.routes)
        if _scopes_in(getattr(ctx, "dependant", None))
        for method in sorted(ctx.methods or set())
        if method in {"POST", "PUT", "PATCH", "DELETE"}
    }

    assert not writes, (
        f"{sorted(writes)} accept an API key and mutate. Before this ships, every "
        "key-authenticated write has to record actor_type=api_key and the key id: add "
        "the column to AuditEvent, stamp it in AuditService.log from a ContextVar the "
        "way impersonator_id already is, and feed it from "
        "CurrentPrincipal.audit_fields(). Then delete this test."
    )


# --- the module toggle -------------------------------------------------------


@pytest.mark.usefixtures("public_api_enabled")
async def test_a_key_stops_working_when_the_module_is_turned_off(
    world: KeyWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row outlives the toggle -- that is what MODULE_TABLES exists to
    tolerate -- so the auth dependency, not the routing table, is what has to
    refuse it."""
    presented = await _mint(world, scopes=["members:read"])
    headers = _key_headers(world, presented)
    assert (await world.client.get(_members_url(world), headers=headers)).status_code == 200

    # Layered over the fixture's patch and undone with it.
    monkeypatch.setattr(
        product_config,
        "MODULES",
        replace(product_config.MODULES, public_api=ModuleConfig(enabled=False)),
    )
    module_loader.get_modules.cache_clear()

    assert (await world.client.get(_members_url(world), headers=headers)).status_code == 401


# --- last_used_at ------------------------------------------------------------


@pytest.mark.usefixtures("public_api_enabled")
async def test_last_used_at_is_written_once_across_ten_rapid_requests(
    world: KeyWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write per request on a hot key is an N+1 wearing a different hat.
    The throttle is a Redis SET NX EX, so it holds across replicas rather
    than per process.

    The per-key rate limit is raised out of the way first: `.env.test` sets it
    to 5 so T-312's own tests can exhaust it cheaply, and ten requests would
    otherwise be refused by the thing this test is not about.
    """
    monkeypatch.setattr(get_settings(), "API_KEY_RATE_LIMIT_PER_MINUTE", 1000)
    presented = await _mint(world, scopes=["members:read"])
    headers = _key_headers(world, presented)

    assert (await world.client.get(_members_url(world), headers=headers)).status_code == 200
    first = (await world.session.execute(_LAST_USED)).scalar_one()
    assert first is not None

    for _ in range(9):
        assert (await world.client.get(_members_url(world), headers=headers)).status_code == 200

    # Unchanged to the microsecond: the nine later requests found the Redis
    # lock already held and wrote nothing at all.
    assert (await world.session.execute(_LAST_USED)).scalar_one() == first

    # ...and the throttle is what holds it, not something accidental. Drop
    # the lock and the very next request stamps it again.
    await world.redis.flushdb()
    assert (await world.client.get(_members_url(world), headers=headers)).status_code == 200
    assert (await world.session.execute(_LAST_USED)).scalar_one() > first
