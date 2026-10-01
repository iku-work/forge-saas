"""
The api_keys module's management surface and its model (T-310).

Grown from the `make new-module` scaffold, and the three §0.8 non-negotiables
it pre-wrote are still here, still first: cross-tenant isolation, the RBAC
gate, and cursor pagination. Everything below them is about the one thing
this module has that a CRUD module does not -- a credential.

The module under test is exercised through a small FastAPI app assembled
here rather than through `app.main`, and for this module that is not merely
the scaffold's convenience: `public_api` ships **off**, so `build_router()`
does not register these routes in the app every other test file talks to.
Standing the router up here is what lets the management surface be tested at
the committed configuration; CI's `module-matrix` combination C runs the same
routes registered for real. `get_current_org` is the one dependency
overridden -- everything downstream of it, `require_permission`'s real RBAC
matrix included, runs for real.

The api_key *table* has the same shape of problem: `migration_targets()`
skips the `public_api` branch while the module is off, so the shared `engine`
fixture never creates it. `_api_key_table` below creates it with
`checkfirst`, which is a no-op in any configuration where the migration has
actually run.
"""

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from limits.storage import RedisStorage
from redis.asyncio import Redis
from sqlalchemy import Select, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlmodel import SQLModel

from app.core.db import get_session
from app.core.dependencies import get_current_org, get_redis
from app.core.handlers import register_exception_handlers
from app.middleware.rate_limit import register_rate_limiting
from app.models._mixins import utcnow
from app.modules.audit.models import AuditEvent
from app.modules.auth.permissions import MemberRole
from app.modules.auth.repository import UserRepository
from app.modules.auth.utils import hash_password
from app.modules.tenancy.models import Membership, Organization
from app.modules.tenancy.repository import MembershipRepository, OrganizationRepository

# The module under test, deliberately in its own import block: where
# `app.modules.api_keys` sorts against `app.modules.auth` above is not what
# this split is about -- it is the scaffold's, and it is kept.
# isort: split
from app.modules.api_keys import service as api_key_service
from app.modules.api_keys.models import KEY_PREFIX, PREFIX_LENGTH, ApiKey
from app.modules.api_keys.repository import ApiKeyRepository
from app.modules.api_keys.router import router
from tests._sessions import reloading, rolling_back

_PREFIX = "/api/v1/api-keys"
_SCOPES = ["orgs:read"]


@pytest.fixture(autouse=True)
async def _api_key_table(engine: AsyncEngine) -> None:
    # checkfirst by default, so this is a no-op wherever the real migration
    # has run (i.e. any product with `public_api` on). No matching drop_all:
    # from that point the table belongs to the migration, and dropping it
    # here would pull it out from under every later test in the
    # session-scoped engine.
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all, tables=[ApiKey.__table__])


async def _make_org(session: AsyncSession, role: MemberRole) -> tuple[Organization, Membership]:
    user = await UserRepository(session).create(
        email=f"{uuid4()}@example.com",
        hashed_password=hash_password("Correct-Horse9"),
        email_verified_at=datetime.now(UTC),
    )
    org = await OrganizationRepository(session).create(name="Acme", slug=f"acme-{uuid4()}")
    membership = await MembershipRepository(session).create(
        user_id=user.id, org_id=org.id, role=role
    )
    await session.flush()
    return org, membership


ClientFactory = Callable[[tuple[Organization, Membership]], httpx.AsyncClient]


@pytest.fixture
async def api_client(
    session: AsyncSession,
    redis_client: Redis,
    rate_limit_storage: RedisStorage,
) -> AsyncIterator[ClientFactory]:
    """Builds a client that calls this module's routes as a given org+member."""
    rate_limit_storage.reset()
    clients: list[httpx.AsyncClient] = []

    def _make(caller: tuple[Organization, Membership]) -> httpx.AsyncClient:
        api = FastAPI()
        register_exception_handlers(api)  # DomainError -> status code + envelope
        register_rate_limiting(api)  # @api_limit needs app.state.limiter
        api.include_router(router, prefix="/api/v1")
        api.dependency_overrides[get_session] = rolling_back(session)
        api.dependency_overrides[get_redis] = lambda: redis_client
        api.dependency_overrides[get_current_org] = reloading(session, caller)
        client = httpx.AsyncClient(transport=ASGITransport(app=api), base_url="https://testserver")
        clients.append(client)
        return client

    yield _make

    for client in clients:
        await client.aclose()


async def _seed_key(
    session: AsyncSession, caller: tuple[Organization, Membership], name: str
) -> ApiKey:
    org, membership = caller
    key = await ApiKeyRepository(session, org.id).create(
        name=name,
        prefix=uuid4().hex[:PREFIX_LENGTH],
        key_hash=api_key_service.hash_secret("whatever"),
        scopes=_SCOPES,
        created_by=membership.user_id,
    )
    await session.flush()
    return key


# --- §0.8 non-negotiable 1: cross-tenant isolation ---------------------------


async def test_one_org_cannot_see_or_fetch_another_orgs_keys(
    session: AsyncSession, api_client: ClientFactory
) -> None:
    owner_a = await _make_org(session, MemberRole.OWNER)
    owner_b = await _make_org(session, MemberRole.OWNER)
    key = await _seed_key(session, owner_a, "org a's key")

    client_b = api_client(owner_b)

    listed = await client_b.get(_PREFIX)
    assert listed.status_code == 200
    assert listed.json()["data"] == []

    # 404, not 403: a cross-tenant probe must not be able to tell the
    # difference between "not yours" and "does not exist".
    fetched = await client_b.get(f"{_PREFIX}/{key.id}")
    assert fetched.status_code == 404
    assert fetched.json()["error"]["code"] == "not_found"

    assert (await client_b.patch(f"{_PREFIX}/{key.id}", json={"name": "x"})).status_code == 404
    assert (await client_b.post(f"{_PREFIX}/{key.id}/revoke")).status_code == 404
    assert (await client_b.delete(f"{_PREFIX}/{key.id}")).status_code == 404


# --- §0.8 non-negotiable 2: the RBAC gate ------------------------------------


async def test_a_plain_member_can_neither_read_nor_mint_keys(
    session: AsyncSession, api_client: ClientFactory
) -> None:
    """Unlike the scaffold's example module, reading is gated too.

    The list shows every prefix and every scope in the organization -- what
    each credential can reach -- which is settings-level information, not
    membership-level. Both routes are SETTINGS_MANAGE for that reason, and a
    plain member holds neither.
    """
    member = await _make_org(session, MemberRole.MEMBER)
    client = api_client(member)

    listed = await client.get(_PREFIX)
    assert listed.status_code == 403

    created = await client.post(_PREFIX, json={"name": "nope", "scopes": _SCOPES})
    assert created.status_code == 403
    assert created.json()["error"]["code"] == "permission_denied"


async def test_an_owner_can_mint(session: AsyncSession, api_client: ClientFactory) -> None:
    owner = await _make_org(session, MemberRole.OWNER)
    client = api_client(owner)

    created = await client.post(_PREFIX, json={"name": "first", "scopes": _SCOPES})

    assert created.status_code == 200
    body = created.json()
    assert body["data"]["api_key"]["name"] == "first"
    assert body["data"]["api_key"]["scopes"] == _SCOPES
    assert body["error"] is None
    assert body["meta"]["request_id"]  # every response carries the envelope (§0.5)


# --- §0.8 non-negotiable 3: cursor pagination --------------------------------


async def test_list_pages_by_cursor_without_repeating_or_dropping_rows(
    session: AsyncSession, api_client: ClientFactory
) -> None:
    owner = await _make_org(session, MemberRole.OWNER)
    for index in range(3):
        await _seed_key(session, owner, f"key {index}")
    client = api_client(owner)

    first = (await client.get(_PREFIX, params={"limit": 2})).json()
    assert len(first["data"]) == 2
    assert first["meta"]["next_cursor"] is not None

    second = (
        await client.get(_PREFIX, params={"limit": 2, "after": first["meta"]["next_cursor"]})
    ).json()
    assert len(second["data"]) == 1
    assert second["meta"]["next_cursor"] is None

    seen = [row["id"] for row in first["data"] + second["data"]]
    assert len(set(seen)) == 3


# --- the credential itself ---------------------------------------------------


async def test_the_full_key_is_returned_once_and_never_again(
    session: AsyncSession, api_client: ClientFactory
) -> None:
    """The contract T-310 is built around: create returns the key, every
    later read returns the prefix and nothing else."""
    owner = await _make_org(session, MemberRole.OWNER)
    client = api_client(owner)

    created = (await client.post(_PREFIX, json={"name": "once", "scopes": _SCOPES})).json()["data"]
    presented = created["key"]
    key_id = created["api_key"]["id"]

    assert presented.startswith(KEY_PREFIX)
    prefix = created["api_key"]["prefix"]
    assert presented.startswith(f"{KEY_PREFIX}{prefix}_")

    parsed = api_key_service.split_presented_key(presented)
    assert parsed is not None
    _, secret = parsed

    fetched = (await client.get(f"{_PREFIX}/{key_id}")).json()["data"]
    listed = (await client.get(_PREFIX)).json()["data"][0]

    for payload in (fetched, listed):
        # Not "the key field is absent" but "the secret appears nowhere in
        # the response at all" -- a future field that happened to echo it
        # would slip past the narrower assertion.
        assert secret not in str(payload)
        assert "key" not in payload
        assert "key_hash" not in payload


async def test_the_stored_digest_is_sha256_of_the_secret_and_nothing_else(
    session: AsyncSession, api_client: ClientFactory
) -> None:
    owner = await _make_org(session, MemberRole.OWNER)
    client = api_client(owner)

    created = (await client.post(_PREFIX, json={"name": "hash", "scopes": _SCOPES})).json()["data"]
    presented = created["key"]
    parsed = api_key_service.split_presented_key(presented)
    assert parsed is not None
    prefix, secret = parsed

    stored = (await client.get(f"{_PREFIX}/{created['api_key']['id']}")).json()["data"]
    assert stored["prefix"] == prefix

    # Read the column straight out of the table rather than through the
    # repository: what is being asserted is what the *database* holds, and a
    # model property could in principle compute it on the way out.
    digest = (
        await session.execute(
            text("SELECT key_hash FROM api_key WHERE prefix = :prefix"), {"prefix": prefix}
        )
    ).scalar_one()
    assert digest == api_key_service.hash_secret(secret)
    # 64 hex characters. Asserted because "it is a digest" is the property
    # that makes the column safe to store at all, and a future switch to
    # something reversible would pass every other test in this file.
    assert len(digest) == 64
    assert secret not in digest


# Enough mints that the bug this guards against cannot hide. The prefix was
# `secrets.token_urlsafe(...)`, whose alphabet includes `_` -- the very
# character that separates the prefix from the secret -- so roughly one key in
# six split in the wrong place and could never authenticate. At that rate a
# single mint passes five times in six; forty pass together with probability
# (5/6)^40, about 0.07%. Forty rather than four hundred because each one is a
# real HTTP round trip and the org cap is 50, so they cannot share an org.
_PARSE_ATTEMPTS = 40


@pytest.mark.parametrize("attempt", range(_PARSE_ATTEMPTS), ids=lambda n: f"mint {n}")
async def test_every_minted_key_parses_back_into_its_two_halves(
    session: AsyncSession, api_client: ClientFactory, attempt: int
) -> None:
    """A found bug, kept.

    The failure mode is the worst possible frequency for an auth path: rare
    enough to look like a fluke, common enough to be constant. The property
    asserted is exact rather than statistical -- a presented key must split
    back into exactly the prefix that was stored -- and the repetition is only
    there because the generator is random.
    """
    owner = await _make_org(session, MemberRole.OWNER)
    client = api_client(owner)

    body = {"name": f"parse {attempt}", "scopes": _SCOPES}
    created = (await client.post(_PREFIX, json=body)).json()["data"]

    parsed = api_key_service.split_presented_key(created["key"])
    assert parsed is not None, created["key"]
    assert parsed[0] == created["api_key"]["prefix"]
    assert api_key_service.hash_secret(parsed[1])


async def test_a_digest_comparison_rejects_a_key_off_by_one_character() -> None:
    secret = "a" * 43
    assert api_key_service.hash_secret(secret) != api_key_service.hash_secret("b" + secret[1:])


async def test_the_model_never_prints_its_digest() -> None:
    """RedactedReprMixin, T-310. A row reaches a traceback or a Sentry event
    through repr, and a credential digest has no business in either."""
    key = ApiKey(
        org_id=uuid4(),
        name="printed",
        prefix="abc",
        key_hash="deadbeef" * 8,
        scopes=_SCOPES,
    )

    printed = repr(key)

    assert "deadbeef" not in printed
    assert "[REDACTED]" in printed
    assert "printed" in printed  # everything else is still readable


# --- lifecycle ---------------------------------------------------------------


async def test_revoking_keeps_the_row_and_stamps_it(
    session: AsyncSession, api_client: ClientFactory
) -> None:
    owner = await _make_org(session, MemberRole.OWNER)
    key = await _seed_key(session, owner, "doomed")
    client = api_client(owner)

    revoked = (await client.post(f"{_PREFIX}/{key.id}/revoke")).json()["data"]
    assert revoked["revoked_at"] is not None

    # Still listed -- that is the whole difference from a delete. "This key
    # was revoked on the 3rd and could reach X" is the question an incident
    # review asks.
    listed = (await client.get(_PREFIX)).json()["data"]
    assert [row["id"] for row in listed] == [str(key.id)]

    first_stamp = revoked["revoked_at"]
    again = (await client.post(f"{_PREFIX}/{key.id}/revoke")).json()["data"]
    assert again["revoked_at"] == first_stamp  # idempotent


async def test_deleting_hides_the_row(session: AsyncSession, api_client: ClientFactory) -> None:
    owner = await _make_org(session, MemberRole.OWNER)
    key = await _seed_key(session, owner, "gone")
    client = api_client(owner)

    assert (await client.delete(f"{_PREFIX}/{key.id}")).status_code == 200

    assert (await client.get(_PREFIX)).json()["data"] == []
    assert (await client.get(f"{_PREFIX}/{key.id}")).status_code == 404


async def test_an_unknown_scope_is_refused_by_name(
    session: AsyncSession, api_client: ClientFactory
) -> None:
    owner = await _make_org(session, MemberRole.OWNER)
    client = api_client(owner)

    refused = await client.post(_PREFIX, json={"name": "bad", "scopes": ["orgs:destroy"]})

    assert refused.status_code == 422
    # The message names the offending value. A caller that mistyped one scope
    # out of six should not have to diff two lists to find out which.
    assert "orgs:destroy" in refused.json()["error"]["message"]


async def test_an_expiry_in_the_past_is_refused(
    session: AsyncSession, api_client: ClientFactory
) -> None:
    owner = await _make_org(session, MemberRole.OWNER)
    client = api_client(owner)

    refused = await client.post(
        _PREFIX,
        json={
            "name": "stale",
            "scopes": _SCOPES,
            "expires_at": (utcnow() - timedelta(days=1)).isoformat(),
        },
    )

    assert refused.status_code == 422


async def test_scopes_cannot_be_widened_by_a_patch(
    session: AsyncSession, api_client: ClientFactory
) -> None:
    """There is no `scopes` field on the update schema, and that is the
    design: widening a live key's access is the same act as minting a bigger
    one, minus the audit trail and minus the chance to rotate the secret."""
    owner = await _make_org(session, MemberRole.OWNER)
    key = await _seed_key(session, owner, "narrow")
    client = api_client(owner)

    patched = await client.patch(
        f"{_PREFIX}/{key.id}", json={"name": "renamed", "scopes": ["members:write"]}
    )

    assert patched.status_code == 200
    assert patched.json()["data"]["name"] == "renamed"
    assert patched.json()["data"]["scopes"] == _SCOPES


async def test_the_org_is_capped(
    session: AsyncSession, api_client: ClientFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cap the UI alone knows about is not a cap (T-313 renders it; this
    enforces it). Monkeypatched down from 50 so the test is one insert, not
    fifty."""
    monkeypatch.setattr(api_key_service, "MAX_KEYS_PER_ORG", 1)
    owner = await _make_org(session, MemberRole.OWNER)
    await _seed_key(session, owner, "the only one")
    client = api_client(owner)

    refused = await client.post(_PREFIX, json={"name": "one too many", "scopes": _SCOPES})

    assert refused.status_code == 402
    assert refused.json()["error"]["code"] == "plan_limit_reached"


async def test_a_revoked_key_still_counts_against_the_cap(
    session: AsyncSession, api_client: ClientFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Revoked keys stay in the list, so a cap that ignored them would let the
    page grow without bound -- which is the reason T-313 can say a
    fifty-row list needs no virtualization."""
    monkeypatch.setattr(api_key_service, "MAX_KEYS_PER_ORG", 1)
    owner = await _make_org(session, MemberRole.OWNER)
    key = await _seed_key(session, owner, "revoked but present")
    # Read before the 402 below rolls back the shared session and expires it.
    key_id = key.id
    client = api_client(owner)
    await client.post(f"{_PREFIX}/{key_id}/revoke")

    refused = await client.post(_PREFIX, json={"name": "next", "scopes": _SCOPES})
    assert refused.status_code == 402

    # Deleting it does free a slot: a deleted key is gone from the list too.
    await client.delete(f"{_PREFIX}/{key_id}")
    assert (await client.post(_PREFIX, json={"name": "next", "scopes": _SCOPES})).status_code == 200


# --- the audit trail ---------------------------------------------------------


async def test_minting_and_revoking_are_audited_without_the_secret(
    session: AsyncSession, api_client: ClientFactory
) -> None:
    owner = await _make_org(session, MemberRole.OWNER)
    org, _ = owner
    client = api_client(owner)

    created = (await client.post(_PREFIX, json={"name": "audited", "scopes": _SCOPES})).json()[
        "data"
    ]
    await client.post(f"{_PREFIX}/{created['api_key']['id']}/revoke")

    events = (await session.execute(_audit_events_for(org.id))).scalars().all()
    actions = [event.action for event in events]
    assert "api_key.created" in actions
    assert "api_key.revoked" in actions

    secret = api_key_service.split_presented_key(created["key"])
    assert secret is not None
    for event in events:
        # An audit feed is read by more people than a database is.
        assert secret[1] not in str(event.diff)


def _audit_events_for(org_id: UUID) -> Select[tuple[AuditEvent]]:
    event = cast("Any", AuditEvent)
    return select(AuditEvent).where(event.org_id == org_id, event.resource_type == "api_key")
