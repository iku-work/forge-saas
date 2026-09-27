"""
The `outbound_webhooks` module's management surface, its models and its
signing scheme (T-320/T-322).

Same shape as tests/modules/test_api_keys.py, and for the same two reasons:
the module ships **off**, so `build_router()` does not register these routes
in the app every other test file talks to (the router is stood up in a
throwaway FastAPI app here instead), and `migration_targets()` skips the
`outbound_webhooks` branch, so the shared `engine` fixture never creates the
tables.

The table fixture is **autouse and depends on `engine` alone**, which is not
a style choice -- it is the deadlock T-310 found. `CREATE TABLE ...
REFERENCES organization(id)` takes SHARE ROW EXCLUSIVE on `organization`,
which conflicts with the ROW EXCLUSIVE the `session` fixture's still-open
outer transaction holds once a test has inserted an org. Creating the tables
lazily therefore hangs forever with no output.
"""

import json
import socket
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from limits.storage import RedisStorage
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlmodel import SQLModel

from app.core import url_guard
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

# isort: split
from app.modules.webhooks import signing
from app.modules.webhooks.models import WebhookDelivery, WebhookEndpoint
from app.modules.webhooks.repository import WebhookEndpointRepository
from app.modules.webhooks.router import router
from app.modules.webhooks.service import MAX_ENDPOINTS_PER_ORG, generate_secret

_PREFIX = "/api/v1/webhooks"
_EVENTS = ["member.invited"]
_URL = "https://consumer.example.com/hook"

# The same fake-DNS table tests/test_url_guard.py uses, and for the same
# reason: `validate_outbound_url` resolves every hostname, so without this
# every case here would depend on whether the machine running it has DNS --
# and `consumer.example.com` resolves nowhere, so a "this URL is fine" test
# would fail for a reason that has nothing to do with webhooks. Literal IPs
# never reach getaddrinfo (see url_guard.py), so the SSRF cases below are
# unaffected by the patch.
#
# It replaces `_getaddrinfo` *inside app.core.url_guard* rather than
# `socket.getaddrinfo` globally, which test_url_guard.py can afford to do and
# this file cannot: these tests hold a real Postgres connection, and a global
# patch answers asyncpg's own lookup with a fabricated `(ip, 0)` sockaddr --
# port zero, i.e. `OSError: [WinError 10049]` before the first assertion runs.
_FAKE_DNS: dict[str, str] = {
    "consumer.example.com": "93.184.216.34",
    "localhost": "127.0.0.1",
    "rebinding.internal.test": "10.0.0.5",
}


def _fake_getaddrinfo(host: str, *_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
    if host not in _FAKE_DNS:
        raise socket.gaierror(socket.EAI_NONAME, "fake DNS: no such host")
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_FAKE_DNS[host], 0))]


@pytest.fixture(autouse=True)
def _fake_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(host: str) -> list[object]:
        return _fake_getaddrinfo(host)

    monkeypatch.setattr(url_guard, "_getaddrinfo", _fake)


@pytest.fixture(autouse=True)
async def _webhook_tables(engine: AsyncEngine) -> None:
    # See the module docstring: autouse, and on `engine` alone, so this runs
    # before any test has inserted an organization. checkfirst by default, so
    # it is a no-op wherever the real migration has run.
    async with engine.begin() as conn:
        await conn.run_sync(
            SQLModel.metadata.create_all,
            tables=[WebhookEndpoint.__table__, WebhookDelivery.__table__],
        )


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
    _redis_client: Redis,
    _rate_limit_storage: RedisStorage,
) -> AsyncIterator[ClientFactory]:
    _rate_limit_storage.reset()
    clients: list[httpx.AsyncClient] = []

    def _make(caller: tuple[Organization, Membership]) -> httpx.AsyncClient:
        api = FastAPI()
        register_exception_handlers(api)
        register_rate_limiting(api)
        api.include_router(router, prefix="/api/v1")
        api.dependency_overrides[get_session] = lambda: session
        api.dependency_overrides[get_redis] = lambda: _redis_client
        api.dependency_overrides[get_current_org] = lambda: caller
        client = httpx.AsyncClient(transport=ASGITransport(app=api), base_url="https://testserver")
        clients.append(client)
        return client

    yield _make

    for client in clients:
        await client.aclose()


async def _seed_endpoint(
    session: AsyncSession,
    caller: tuple[Organization, Membership],
    url: str = _URL,
    **overrides: object,
) -> WebhookEndpoint:
    org, membership = caller
    endpoint = await WebhookEndpointRepository(session, org.id).create(
        url=url,
        secret=generate_secret(),
        event_types=_EVENTS,
        created_by=membership.user_id,
        **overrides,
    )
    await session.flush()
    return endpoint


# --- §0.8 non-negotiable 1: cross-tenant isolation ---------------------------


async def test_one_org_cannot_see_or_touch_another_orgs_endpoints(
    session: AsyncSession, api_client: ClientFactory
) -> None:
    owner_a = await _make_org(session, MemberRole.OWNER)
    owner_b = await _make_org(session, MemberRole.OWNER)
    endpoint = await _seed_endpoint(session, owner_a)

    client_b = api_client(owner_b)

    listed = await client_b.get(f"{_PREFIX}/endpoints")
    assert listed.status_code == 200
    assert listed.json()["data"] == []

    # 404, not 403: a cross-tenant probe must not be able to tell "not yours"
    # from "does not exist".
    assert (await client_b.get(f"{_PREFIX}/endpoints/{endpoint.id}")).status_code == 404
    assert (
        await client_b.patch(f"{_PREFIX}/endpoints/{endpoint.id}", json={"enabled": False})
    ).status_code == 404
    assert (
        await client_b.post(f"{_PREFIX}/endpoints/{endpoint.id}/rotate-secret")
    ).status_code == 404
    assert (await client_b.delete(f"{_PREFIX}/endpoints/{endpoint.id}")).status_code == 404
    assert (await client_b.get(f"{_PREFIX}/endpoints/{endpoint.id}/deliveries")).status_code == 404


async def test_the_delivery_log_still_pages_from_an_index_at_ten_thousand_rows(
    session: AsyncSession,
) -> None:
    """`webhook_delivery` is the fastest-growing table this template adds --
    one row per event per endpoint -- so "it paginates" has to mean something
    at a size nobody has locally.

    The statement *count* is already fixed by tests/test_query_budgets.py,
    which measures at 3 rows and again at 30 and fails if the two differ. That
    catches an N+1 and cannot catch this: a keyset query whose index has been
    dropped, reordered, or made unusable by a change to the ORDER BY issues
    exactly one statement either way, and simply reads the whole table to do
    it. What changes is the *plan*, so that is what this asserts.

    EXPLAIN rather than a timing assertion, because a wall-clock budget on a
    shared CI runner is a flake generator, and "how long did it take" is a
    worse question than "did it have to sort the table to answer".
    """
    org, owner = await _make_org(session, MemberRole.OWNER)
    endpoint = await _seed_endpoint(session, (org, owner))
    await session.flush()

    # Bulk-inserted in one statement: 10k rows through the ORM would dominate
    # this test's runtime and prove nothing extra.
    await session.execute(
        text("""
            INSERT INTO webhook_delivery
                (id, org_id, endpoint_id, event_type, payload, status,
                 attempt_count, delivery_id, created_at, updated_at)
            SELECT gen_random_uuid(), :org_id, :endpoint_id, 'member.invited',
                   '{}'::jsonb, 'delivered', 1, gen_random_uuid(),
                   now() - (i * interval '1 second'),
                   now()
            FROM generate_series(1, 10000) AS i
        """),
        {"org_id": org.id, "endpoint_id": endpoint.id},
    )
    # Without stats the planner assumes a tiny table and picks a sequential
    # scan on merit, which would make this assert the wrong thing.
    await session.execute(text("ANALYZE webhook_delivery"))

    plan = (
        await session.execute(
            text("""
                EXPLAIN (FORMAT JSON)
                SELECT * FROM webhook_delivery
                 WHERE org_id = :org_id
                   AND endpoint_id = :endpoint_id
                   AND deleted_at IS NULL
                 ORDER BY created_at DESC, id DESC
                 LIMIT 21
            """),
            {"org_id": org.id, "endpoint_id": endpoint.id},
        )
    ).scalar_one()

    rendered = json.dumps(plan)
    assert "Seq Scan" not in rendered, (
        "the delivery log is reading the whole table to serve one page:\n" + rendered
    )
    # The index that exists for exactly this query
    # (WebhookDelivery.__table_args__). Named rather than merely asserting
    # "some index", so that dropping this one and leaving another is a
    # failure rather than a silently slower plan.
    assert "ix_webhook_delivery_endpoint_id_created_at_id" in rendered, rendered


# --- §0.8 non-negotiable 2: the RBAC gate ------------------------------------


async def test_a_plain_member_can_neither_read_nor_register_endpoints(
    session: AsyncSession, api_client: ClientFactory
) -> None:
    """Reading is gated too, like the API-key list and for the same reason:
    the delivery log carries payloads, and the endpoint list says where an
    organization's data is being pushed."""
    member = await _make_org(session, MemberRole.MEMBER)
    client = api_client(member)

    assert (await client.get(f"{_PREFIX}/endpoints")).status_code == 403
    created = await client.post(f"{_PREFIX}/endpoints", json={"url": _URL, "event_types": _EVENTS})
    assert created.status_code == 403
    assert created.json()["error"]["code"] == "permission_denied"


# --- the secret --------------------------------------------------------------


async def test_the_signing_secret_is_returned_once_and_never_again(
    session: AsyncSession, api_client: ClientFactory
) -> None:
    owner = await _make_org(session, MemberRole.OWNER)
    client = api_client(owner)

    created = await client.post(f"{_PREFIX}/endpoints", json={"url": _URL, "event_types": _EVENTS})
    assert created.status_code == 200
    body = created.json()["data"]
    secret = body["secret"]
    assert secret.startswith("whsec_")
    endpoint_id = body["endpoint"]["id"]

    fetched = await client.get(f"{_PREFIX}/endpoints/{endpoint_id}")
    assert "secret" not in fetched.json()["data"]
    assert secret not in fetched.text

    listed = await client.get(f"{_PREFIX}/endpoints")
    assert secret not in listed.text


async def test_the_secret_is_absent_from_repr(session: AsyncSession) -> None:
    """A model that lands in a traceback or a Sentry event must not print a
    live HMAC key next to the URL it signs for."""
    owner = await _make_org(session, MemberRole.OWNER)
    endpoint = await _seed_endpoint(session, owner)
    endpoint.secret_previous = "whsec_old"

    rendered = repr(endpoint)
    assert endpoint.secret not in rendered
    assert "whsec_old" not in rendered
    assert rendered.count("[REDACTED]") == 2  # both secret columns


async def test_the_secret_column_is_encrypted_at_rest(session: AsyncSession) -> None:
    """§7's rule, checked against what Postgres actually stores rather than
    against the declared type."""
    owner = await _make_org(session, MemberRole.OWNER)
    endpoint = await _seed_endpoint(session, owner)
    await session.commit()

    # Raw SQL, not `select(WebhookEndpoint.__table__.c.secret)`: the column
    # still carries the EncryptedString type, so going through SQLAlchemy
    # would decrypt on the way out and the assertion would compare the
    # plaintext with itself -- a test that passes on an unencrypted column.
    raw = (
        await session.execute(
            text("SELECT secret FROM webhook_endpoint WHERE id = :id"), {"id": endpoint.id}
        )
    ).scalar_one()
    assert raw != endpoint.secret
    # Fernet tokens are base64url and start with a version byte of 0x80.
    assert raw.startswith("gAAAAA")


# --- SSRF --------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/hook",
        "http://localhost/hook",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/hook",
        "ftp://consumer.example.com/hook",
        "file:///etc/passwd",
        # A hostname whose *text* looks public but resolves privately -- the
        # rebinding case, which is why the guard judges the resolved address
        # rather than the string.
        "https://rebinding.internal.test/hook",
    ],
)
async def test_a_private_or_non_http_url_is_rejected_at_save(
    session: AsyncSession, api_client: ClientFactory, url: str
) -> None:
    owner = await _make_org(session, MemberRole.OWNER)
    client = api_client(owner)

    created = await client.post(f"{_PREFIX}/endpoints", json={"url": url, "event_types": _EVENTS})

    assert created.status_code == 422
    assert created.json()["error"]["code"] == "unprocessable_entity"


async def test_an_unsafe_url_is_also_rejected_on_update(
    session: AsyncSession, api_client: ClientFactory
) -> None:
    owner = await _make_org(session, MemberRole.OWNER)
    endpoint = await _seed_endpoint(session, owner)
    client = api_client(owner)

    updated = await client.patch(
        f"{_PREFIX}/endpoints/{endpoint.id}", json={"url": "http://192.168.0.1/hook"}
    )

    assert updated.status_code == 422


# --- the event registry ------------------------------------------------------


async def test_subscribing_to_an_unregistered_event_names_the_offending_value(
    session: AsyncSession, api_client: ClientFactory
) -> None:
    owner = await _make_org(session, MemberRole.OWNER)
    client = api_client(owner)

    created = await client.post(
        f"{_PREFIX}/endpoints",
        json={"url": _URL, "event_types": ["member.invited", "member.teleported"]},
    )

    assert created.status_code == 422
    assert "member.teleported" in created.json()["error"]["message"]


async def test_the_event_catalogue_is_served_for_the_ui_to_render(
    session: AsyncSession, api_client: ClientFactory
) -> None:
    owner = await _make_org(session, MemberRole.OWNER)
    client = api_client(owner)

    listed = await client.get(f"{_PREFIX}/event-types")

    assert listed.status_code == 200
    names = [entry["name"] for entry in listed.json()["data"]]
    assert "member.invited" in names
    assert all(entry["description"] for entry in listed.json()["data"])


# --- rotation ----------------------------------------------------------------


async def test_rotation_keeps_the_old_secret_valid_for_the_overlap(
    session: AsyncSession, api_client: ClientFactory
) -> None:
    owner = await _make_org(session, MemberRole.OWNER)
    endpoint = await _seed_endpoint(session, owner)
    original = endpoint.secret
    client = api_client(owner)

    rotated = await client.post(f"{_PREFIX}/endpoints/{endpoint.id}/rotate-secret")

    assert rotated.status_code == 200
    body = rotated.json()["data"]
    assert body["secret"] != original
    assert body["previous_secret_expires_at"]

    await session.refresh(endpoint)
    assert endpoint.secret_previous == original
    assert endpoint.secret_previous_expires_at is not None
    assert endpoint.secret_previous_expires_at > utcnow()


async def test_an_expired_previous_secret_stops_signing(session: AsyncSession) -> None:
    """The overlap is a window, not a permanent second key."""
    from app.modules.webhooks.service import WebhookDeliveryService  # noqa: PLC0415

    org, _ = owner = await _make_org(session, MemberRole.OWNER)
    endpoint = await _seed_endpoint(session, owner)
    endpoint.secret_previous = "whsec_old"
    endpoint.secret_previous_expires_at = utcnow() + timedelta(hours=1)
    service = WebhookDeliveryService(session, org.id)

    assert service.active_secrets(endpoint) == [endpoint.secret, "whsec_old"]

    endpoint.secret_previous_expires_at = utcnow() - timedelta(seconds=1)
    assert service.active_secrets(endpoint) == [endpoint.secret]


# --- signing -----------------------------------------------------------------


def test_the_signature_verifies_against_a_known_vector() -> None:
    """A fixed vector, not a round trip through the producer's own helper.

    Round-tripping `build_signature` through `verify_signature` would pass
    even if both sides changed the construction together, which is exactly
    the change that breaks every consumer in the field. The digest below was
    computed once, by hand, from `"1700000000." + body`.
    """
    secret = "whsec_test"
    body = b'{"id":"1","type":"member.invited"}'
    timestamp = 1_700_000_000

    header = signing.build_signature(secret, body, timestamp)

    assert header.startswith(f"t={timestamp},v1=")
    assert signing.signed_message(timestamp, body) == b"1700000000." + body
    assert signing.verify_signature(secret, body, header, now=timestamp)


def test_a_mutated_body_fails_verification() -> None:
    secret = "whsec_test"
    header = signing.build_signature(secret, b'{"amount":1}', 1_700_000_000)

    assert not signing.verify_signature(secret, b'{"amount":2}', header, now=1_700_000_000)


def test_a_stale_timestamp_fails_even_with_a_correct_digest() -> None:
    """The replay defence, and the reason the timestamp is *inside* the signed
    string: a captured request cannot be resent tomorrow."""
    secret = "whsec_test"
    body = b"{}"
    header = signing.build_signature(secret, body, 1_700_000_000)

    assert signing.verify_signature(secret, body, header, now=1_700_000_100)
    assert not signing.verify_signature(secret, body, header, now=1_700_000_000 + 301)


def test_a_request_signed_with_two_secrets_satisfies_a_consumer_holding_either() -> None:
    body = b'{"id":"1"}'
    header = signing.signature_headers(["whsec_new", "whsec_old"], body, 1_700_000_000)

    assert signing.verify_signature("whsec_new", body, header, now=1_700_000_000)
    assert signing.verify_signature("whsec_old", body, header, now=1_700_000_000)
    assert not signing.verify_signature("whsec_other", body, header, now=1_700_000_000)


def test_a_malformed_signature_header_is_rejected_rather_than_parsed() -> None:
    assert signing.parse_signature("nonsense") is None
    assert signing.parse_signature("t=abc,v1=deadbeef") is None
    assert signing.parse_signature("v1=deadbeef") is None
    assert signing.parse_signature("t=1700000000") is None


# --- the delivery log and replay ---------------------------------------------


async def _seed_delivery(
    session: AsyncSession, endpoint: WebhookEndpoint, **overrides: object
) -> WebhookDelivery:
    # The defaults go through a dict rather than straight into the call, so an
    # override of one of them replaces it instead of colliding with it.
    fields: dict[str, object] = {
        "org_id": endpoint.org_id,
        "endpoint_id": endpoint.id,
        "event_type": "member.invited",
        "payload": {"id": str(uuid4()), "type": "member.invited", "data": {"email": "a@b.test"}},
        "status": "failed",
        "attempt_count": 6,
        "response_code": 500,
        "delivery_id": uuid4(),
    }
    fields.update(overrides)
    delivery = WebhookDelivery(**fields)
    session.add(delivery)
    await session.flush()
    return delivery


async def test_the_log_omits_payloads_and_the_detail_read_carries_one(
    session: AsyncSession, api_client: ClientFactory
) -> None:
    """A log page carrying every payload is a response nobody reads and a
    query budget nobody meets."""
    owner = await _make_org(session, MemberRole.OWNER)
    endpoint = await _seed_endpoint(session, owner)
    delivery = await _seed_delivery(session, endpoint)
    client = api_client(owner)

    listed = await client.get(f"{_PREFIX}/endpoints/{endpoint.id}/deliveries")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()["data"]] == [str(delivery.id)]
    assert listed.json()["data"][0]["payload"] is None

    detail = await client.get(f"{_PREFIX}/deliveries/{delivery.id}")
    assert detail.json()["data"]["payload"] == delivery.payload


async def test_the_log_can_be_filtered_by_status(
    session: AsyncSession, api_client: ClientFactory
) -> None:
    owner = await _make_org(session, MemberRole.OWNER)
    endpoint = await _seed_endpoint(session, owner)
    delivered = await _seed_delivery(session, endpoint, status="delivered", response_code=200)
    await _seed_delivery(session, endpoint, status="dead")
    client = api_client(owner)

    filtered = await client.get(
        f"{_PREFIX}/endpoints/{endpoint.id}/deliveries", params={"status": "delivered"}
    )

    assert [row["id"] for row in filtered.json()["data"]] == [str(delivered.id)]


async def test_replay_creates_a_new_delivery_and_leaves_the_original_untouched(
    session: AsyncSession,
    api_client: ClientFactory,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    """The log is an audit trail. "We replayed it and now it says delivered"
    is a sentence this API must not be able to produce."""
    owner = await _make_org(session, MemberRole.OWNER)
    endpoint = await _seed_endpoint(session, owner)
    original = await _seed_delivery(session, endpoint)
    client = api_client(owner)

    replayed = await client.post(f"{_PREFIX}/deliveries/{original.id}/replay")

    assert replayed.status_code == 200
    body = replayed.json()["data"]
    assert body["id"] != str(original.id)
    # A new `X-Forge-Delivery`, deliberately: retries of one delivery share
    # theirs so a consumer can dedupe, and a replay is a request to process
    # the event again.
    assert body["delivery_id"] != str(original.delivery_id)
    assert body["replay_of_id"] == str(original.id)
    assert body["status"] == "pending"
    assert body["attempt_count"] == 0
    # The stored payload, not a rebuilt one -- what happened then, not what
    # the row it described says now.
    assert body["payload"] == original.payload

    await session.refresh(original)
    assert original.status == "failed"
    assert original.attempt_count == 6
    assert original.replay_of_id is None

    # And the new row is actually queued -- a replay that only writes a row
    # is a button that appears to work.
    assert ("deliver_webhook", {"delivery_id": body["id"]}) in enqueued_jobs


async def test_a_disabled_endpoint_cannot_be_replayed_into(
    session: AsyncSession,
    api_client: ClientFactory,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    """Otherwise the replay is queued and then dropped by the job, which
    looks like a replay that worked."""
    owner = await _make_org(session, MemberRole.OWNER)
    endpoint = await _seed_endpoint(session, owner, enabled=False)
    original = await _seed_delivery(session, endpoint)
    client = api_client(owner)

    replayed = await client.post(f"{_PREFIX}/deliveries/{original.id}/replay")

    assert replayed.status_code == 409
    assert replayed.json()["error"]["code"] == "conflict"
    assert enqueued_jobs == []


# --- limits and audit --------------------------------------------------------


async def test_the_endpoint_cap_is_enforced_by_the_service_not_only_the_ui(
    session: AsyncSession, api_client: ClientFactory
) -> None:
    owner = await _make_org(session, MemberRole.OWNER)
    for index in range(MAX_ENDPOINTS_PER_ORG):
        await _seed_endpoint(session, owner, url=f"https://consumer.example.com/{index}")
    client = api_client(owner)

    created = await client.post(f"{_PREFIX}/endpoints", json={"url": _URL, "event_types": _EVENTS})

    assert created.status_code == 402
    assert created.json()["error"]["code"] == "plan_limit_reached"


async def test_no_audit_diff_ever_carries_a_secret(
    session: AsyncSession, api_client: ClientFactory
) -> None:
    owner = await _make_org(session, MemberRole.OWNER)
    client = api_client(owner)

    created = await client.post(f"{_PREFIX}/endpoints", json={"url": _URL, "event_types": _EVENTS})
    secret = created.json()["data"]["secret"]
    endpoint_id = created.json()["data"]["endpoint"]["id"]
    rotated = await client.post(f"{_PREFIX}/endpoints/{endpoint_id}/rotate-secret")
    rotated_secret = rotated.json()["data"]["secret"]

    events = (
        (await session.execute(select(AuditEvent).where(AuditEvent.resource_id == endpoint_id)))
        .scalars()
        .all()
    )
    actions = {event.action for event in events}
    assert "webhook_endpoint.created" in actions
    assert "webhook_endpoint.secret_rotated" in actions
    for event in events:
        assert secret not in str(event.diff)
        assert rotated_secret not in str(event.diff)


async def test_re_enabling_an_endpoint_clears_the_failure_counter(
    session: AsyncSession, api_client: ClientFactory
) -> None:
    """Otherwise the operator's fix trips the breaker again on the next single
    failure, which reads as "your fix did not work"."""
    owner = await _make_org(session, MemberRole.OWNER)
    endpoint = await _seed_endpoint(
        session, owner, enabled=False, consecutive_failures=5, disabled_at=utcnow()
    )
    client = api_client(owner)

    updated = await client.patch(f"{_PREFIX}/endpoints/{endpoint.id}", json={"enabled": True})

    assert updated.status_code == 200
    assert updated.json()["data"]["consecutive_failures"] == 0
    assert updated.json()["data"]["disabled_at"] is None
