"""
T-321's delivery job: the retry ladder, the dead letter, the per-endpoint
breaker, the retention prune, and the queue-depth gauge.

Same shape as test_virus_scan_job.py -- a dedicated engine with just this
job's tables, `async_session_factory` monkeypatched onto the job module, and
a real Redis for the gauge. What is faked is only the network: an
`httpx.MockTransport` standing in for the consumer, installed on the job's
`httpx.AsyncClient` so the request the job actually builds (headers,
signature, body bytes) is the one asserted on.

**No sleeps anywhere.** The ladder is asserted against `next_attempt_at` and
against the `_defer_by` the job hands ARQ, and a "twelve hours later" case is
expressed by moving the row's timestamp rather than by waiting. A retry test
that sleeps is a retry test nobody runs.
"""

import asyncio
import json
import socket
import time
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from app.core import jobs as jobs_module
from app.core import url_guard
from app.core.config import get_settings
from app.core.metrics import WEBHOOK_QUEUE_DEPTH_REDIS_KEY
from app.models._mixins import utcnow
from app.modules.audit.models import AuditEvent
from app.modules.auth.models import User
from app.modules.auth.permissions import MemberRole
from app.modules.notifications.models import Notification
from app.modules.tenancy.models import Membership, Organization
from app.modules.webhooks import signing
from app.modules.webhooks.models import (
    MAX_CONSECUTIVE_FAILURES,
    MAX_RESPONSE_BODY_CHARS,
    WebhookDelivery,
    WebhookEndpoint,
)
from app.modules.webhooks.service import RETRY_SCHEDULE
from app.workers.jobs import webhooks as webhooks_job
from app.workers.jobs.webhooks import (
    deliver_webhook,
    prune_webhook_deliveries,
    sweep_due_webhook_deliveries,
)

_SECRET = "whsec_testsecret"
_URL = "https://93.184.216.34/hook"  # a literal public IP: no DNS in the guard


@pytest.fixture(autouse=True)
def _use_test_engine(monkeypatch: pytest.MonkeyPatch, test_engine: AsyncEngine) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(webhooks_job, "async_session_factory", factory)


@pytest.fixture(autouse=True)
def _module_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """`outbound_webhooks` ships off, and the job refuses to send when it is.

    Patched at the job module rather than by rebuilding the module config:
    what is under test here is the delivery path, and
    tests/modules/test_module_toggles.py already owns the toggle itself. The
    disabled case gets its own test below, which turns this back off.
    """
    monkeypatch.setattr(webhooks_job, "is_enabled", lambda _name: True)


@pytest.fixture
def ctx(redis: Redis) -> dict[str, Any]:
    return {"redis": redis}


@pytest.fixture(autouse=True)
def enqueued(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    """Records what the job hands ARQ, and keeps it away from a real Redis.

    Autouse, because *every* case here would otherwise reach the real
    `enqueue_job` on its first failed delivery -- and a test that
    accidentally talks to a queue is a test that passes on one machine.
    """
    calls: list[tuple[str, dict[str, Any]]] = []

    async def _fake_enqueue(function: str, **kwargs: Any) -> str:  # noqa: ANN401
        calls.append((function, kwargs))
        return f"fake-{len(calls)}"

    monkeypatch.setattr(jobs_module, "enqueue_job", _fake_enqueue)
    return calls


class _Consumer:
    """A fake webhook consumer: a queue of responses, and a record of what it
    was actually sent."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        outcome = self._responses[min(len(self.requests) - 1, len(self._responses) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        try:
            content = outcome.content
        except httpx.ResponseNotRead:
            return outcome  # a streamed body (see _EndlessBody): single-use by design
        # A fresh, *unread* response per request, as a network sends. The job
        # streams the body raw now (review R6); httpx reads a `content=`
        # response the moment it is built, which no real consumer's is, and a
        # stream can only be read once.
        return httpx.Response(
            outcome.status_code, headers=outcome.headers, stream=httpx.ByteStream(content)
        )


# What the `consumer` fixture hands a test: call it with the responses the
# fake consumer should give, get back the recorder.
ConsumerFactory = Callable[..., _Consumer]


@pytest.fixture
def consumer(monkeypatch: pytest.MonkeyPatch) -> ConsumerFactory:
    """Installs a MockTransport on whatever client the job builds.

    Patching the *class* rather than passing a transport in means the request
    under assertion is the one the job actually constructed -- its timeout,
    its redirect policy, its headers -- rather than one the test built to
    look like it.
    """
    real_client = httpx.AsyncClient

    def _install(*responses: httpx.Response | Exception) -> _Consumer:
        fake = _Consumer(*responses)

        def _client(**kwargs: object) -> httpx.AsyncClient:
            kwargs["transport"] = httpx.MockTransport(fake.handler)
            return real_client(**kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(webhooks_job.httpx, "AsyncClient", _client)
        return fake

    return _install


async def _seed(
    session: AsyncSession,
    **endpoint_overrides: Any,  # noqa: ANN401
) -> tuple[WebhookEndpoint, WebhookDelivery]:
    org = Organization(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    session.add(org)
    await session.flush()
    endpoint = WebhookEndpoint(
        org_id=org.id,
        url=_URL,
        secret=_SECRET,
        event_types=["member.invited"],
        **endpoint_overrides,
    )
    session.add(endpoint)
    await session.flush()
    delivery = WebhookDelivery(
        org_id=org.id,
        endpoint_id=endpoint.id,
        event_type="member.invited",
        payload={"id": str(uuid4()), "type": "member.invited", "data": {"email": "a@b.test"}},
        status="pending",
        next_attempt_at=utcnow(),
        delivery_id=uuid4(),
    )
    session.add(delivery)
    await session.commit()
    return endpoint, delivery


async def _reload(session: AsyncSession, delivery_id: UUID) -> WebhookDelivery:
    # expunge, not expire: the job committed through its *own* session, so
    # these instances are stale rather than merely expired -- and expiring
    # them would expire the primary key too, so the next `endpoint.id` would
    # fire a synchronous lazy load from inside async code (MissingGreenlet).
    # Detaching keeps the ids readable and forces `get` to go to the database.
    session.expunge_all()
    row = await session.get(WebhookDelivery, delivery_id)
    assert row is not None
    return row


# --- the happy path ----------------------------------------------------------


async def test_a_2xx_marks_the_delivery_delivered_and_records_the_response(
    session: AsyncSession,
    ctx: dict[str, Any],
    consumer: ConsumerFactory,
    enqueued: list[tuple[str, dict[str, Any]]],
) -> None:
    consumer(httpx.Response(200, text="ok"))
    _, delivery = await _seed(session)

    await deliver_webhook(ctx, delivery_id=str(delivery.id))

    row = await _reload(session, delivery.id)
    assert row.status == "delivered"
    assert row.attempt_count == 1
    assert row.response_code == 200
    assert row.response_body == "ok"
    assert row.delivered_at is not None
    assert row.next_attempt_at is None
    assert enqueued == []  # nothing to retry


async def test_the_request_carries_a_signature_over_the_exact_bytes_it_sends(
    session: AsyncSession, ctx: dict[str, Any], consumer: ConsumerFactory
) -> None:
    """The rule that costs people days: the digest must cover the body as
    sent, not a re-serialisation of the same object."""
    fake = consumer(httpx.Response(200))
    _, delivery = await _seed(session)

    await deliver_webhook(ctx, delivery_id=str(delivery.id))

    request = fake.requests[0]
    header = request.headers[signing.SIGNATURE_HEADER]
    assert signing.verify_signature(_SECRET, request.content, header)
    assert request.headers[signing.EVENT_HEADER] == "member.invited"
    assert request.headers[signing.DELIVERY_HEADER] == str(delivery.delivery_id)
    # And the body really is the stored payload, not a rebuilt one.
    assert json.loads(request.content) == delivery.payload


async def test_a_rotation_overlap_signs_with_both_secrets(
    session: AsyncSession, ctx: dict[str, Any], consumer: ConsumerFactory
) -> None:
    fake = consumer(httpx.Response(200))
    _, delivery = await _seed(
        session,
        secret_previous="whsec_old",
        secret_previous_expires_at=utcnow() + timedelta(hours=1),
    )

    await deliver_webhook(ctx, delivery_id=str(delivery.id))

    header = fake.requests[0].headers[signing.SIGNATURE_HEADER]
    body = fake.requests[0].content
    assert signing.verify_signature(_SECRET, body, header)
    assert signing.verify_signature("whsec_old", body, header)


# --- the ladder --------------------------------------------------------------


async def test_a_500_schedules_the_next_rung_and_re_enqueues_the_same_delivery(
    session: AsyncSession,
    ctx: dict[str, Any],
    consumer: ConsumerFactory,
    enqueued: list[tuple[str, dict[str, Any]]],
) -> None:
    consumer(httpx.Response(500, text="boom"))
    _, delivery = await _seed(session)
    before = utcnow()

    await deliver_webhook(ctx, delivery_id=str(delivery.id))

    row = await _reload(session, delivery.id)
    assert row.status == "pending"
    assert row.attempt_count == 1
    assert row.response_code == 500
    assert row.next_attempt_at is not None
    # The first rung is one minute.
    assert timedelta(seconds=50) < row.next_attempt_at - before < timedelta(seconds=70)

    assert len(enqueued) == 1
    function, kwargs = enqueued[0]
    assert function == "deliver_webhook"
    assert kwargs["delivery_id"] == str(delivery.id)
    # The defer is computed from the row, so the queue and the log can never
    # disagree about when the next attempt is.
    assert 0 < kwargs["_defer_by"] <= 60


async def test_every_attempt_reuses_the_same_delivery_id_so_a_consumer_can_dedupe(
    session: AsyncSession, ctx: dict[str, Any], consumer: ConsumerFactory
) -> None:
    fake = consumer(httpx.Response(500), httpx.Response(500), httpx.Response(200))
    _, delivery = await _seed(session)

    for _ in range(3):
        await _make_due(session, delivery.id)
        await deliver_webhook(ctx, delivery_id=str(delivery.id))

    ids = {request.headers[signing.DELIVERY_HEADER] for request in fake.requests}
    assert ids == {str(delivery.delivery_id)}
    assert (await _reload(session, delivery.id)).status == "delivered"


async def test_the_ladder_runs_out_and_the_delivery_is_dead_lettered(
    session: AsyncSession,
    ctx: dict[str, Any],
    consumer: ConsumerFactory,
    enqueued: list[tuple[str, dict[str, Any]]],
) -> None:
    consumer(httpx.Response(500))
    _, delivery = await _seed(session)

    attempts = len(RETRY_SCHEDULE) + 1
    for _ in range(attempts):
        await _make_due(session, delivery.id)
        await deliver_webhook(ctx, delivery_id=str(delivery.id))

    row = await _reload(session, delivery.id)
    assert row.attempt_count == attempts
    assert row.status == "dead"
    assert row.next_attempt_at is None
    # One re-enqueue per *retry*, and none after the last attempt.
    assert len(enqueued) == len(RETRY_SCHEDULE)


async def test_the_whole_ladder_is_the_documented_one(
    session: AsyncSession, ctx: dict[str, Any], consumer: ConsumerFactory
) -> None:
    """1 m, 5 m, 30 m, 2 h, 12 h -- asserted against the gaps the job actually
    writes, because the schedule is a promise made to consumers in the docs."""
    consumer(httpx.Response(503))
    _, delivery = await _seed(session)
    gaps: list[timedelta] = []

    for _ in range(len(RETRY_SCHEDULE)):
        await _make_due(session, delivery.id)
        marker = utcnow()
        await deliver_webhook(ctx, delivery_id=str(delivery.id))
        row = await _reload(session, delivery.id)
        assert row.next_attempt_at is not None
        gaps.append(row.next_attempt_at - marker)

    for actual, expected in zip(gaps, RETRY_SCHEDULE, strict=True):
        assert abs(actual - expected) < timedelta(seconds=10)


async def test_a_timeout_is_recorded_as_a_failure_rather_than_crashing_the_job(
    session: AsyncSession, ctx: dict[str, Any], consumer: ConsumerFactory
) -> None:
    consumer(httpx.ReadTimeout("too slow"))
    _, delivery = await _seed(session)

    await deliver_webhook(ctx, delivery_id=str(delivery.id))

    row = await _reload(session, delivery.id)
    assert row.status == "pending"
    assert row.response_code is None
    assert "ReadTimeout" in (row.error or "")


# --- the per-endpoint breaker ------------------------------------------------


async def test_n_dead_lettered_deliveries_switch_the_endpoint_off(
    session: AsyncSession, ctx: dict[str, Any], consumer: ConsumerFactory
) -> None:
    """Counted per *dead letter*, not per attempt: one consumer restart
    produces five failed attempts of the same delivery, and an endpoint
    disabled by that is an endpoint disabled by a deploy."""
    consumer(httpx.Response(500))
    endpoint, first = await _seed(session)

    for index in range(MAX_CONSECUTIVE_FAILURES):
        delivery = first if index == 0 else await _extra_delivery(session, endpoint)
        for _ in range(len(RETRY_SCHEDULE) + 1):
            await _make_due(session, delivery.id)
            await deliver_webhook(ctx, delivery_id=str(delivery.id))

    session.expunge_all()
    refreshed = await session.get(WebhookEndpoint, endpoint.id)
    assert refreshed is not None
    assert refreshed.consecutive_failures == MAX_CONSECUTIVE_FAILURES
    assert refreshed.enabled is False
    assert refreshed.disabled_at is not None

    events = (
        (
            await session.execute(
                select(AuditEvent).where(AuditEvent.action == "webhook_endpoint.auto_disabled")
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1


@pytest.mark.module_dependent
async def test_the_orgs_owners_are_told_when_an_endpoint_is_switched_off(
    session: AsyncSession, ctx: dict[str, Any], consumer: ConsumerFactory
) -> None:
    """The half of the auto-disable a developer actually notices.

    Guarded on the `notifications` module being enabled, which the committed
    config has and the everything-off configuration does not -- hence the
    marker, without which this asserts a notification nothing was ever going
    to send. `test_a_disabled_notifications_module_is_not_an_outage` below is
    the unmarked half, and covers the other side.
    """
    consumer(httpx.Response(500))
    endpoint, first = await _seed(session)
    owner = await _add_owner(session, endpoint.org_id)

    for index in range(MAX_CONSECUTIVE_FAILURES):
        delivery = first if index == 0 else await _extra_delivery(session, endpoint)
        for _ in range(len(RETRY_SCHEDULE) + 1):
            await _make_due(session, delivery.id)
            await deliver_webhook(ctx, delivery_id=str(delivery.id))

    notifications = (
        (await session.execute(select(Notification).where(Notification.user_id == owner.id)))
        .scalars()
        .all()
    )
    assert [n.type for n in notifications] == ["webhook_endpoint_disabled"]


async def test_a_disabled_notifications_module_is_not_an_outage(
    session: AsyncSession,
    ctx: dict[str, Any],
    consumer: ConsumerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A product with `notifications` off still auto-disables the endpoint --
    silently, which is the correct degradation: the row is visibly disabled
    and the audit event still records why."""
    from app.modules.webhooks import service as webhook_service  # noqa: PLC0415

    consumer(httpx.Response(500))
    monkeypatch.setattr(webhook_service, "is_enabled", lambda _name: False)
    endpoint, first = await _seed(session)
    await _add_owner(session, endpoint.org_id)

    for index in range(MAX_CONSECUTIVE_FAILURES):
        delivery = first if index == 0 else await _extra_delivery(session, endpoint)
        for _ in range(len(RETRY_SCHEDULE) + 1):
            await _make_due(session, delivery.id)
            await deliver_webhook(ctx, delivery_id=str(delivery.id))

    session.expunge_all()
    refreshed = await session.get(WebhookEndpoint, endpoint.id)
    assert refreshed is not None
    assert refreshed.enabled is False
    assert (await session.execute(select(Notification))).scalars().all() == []


async def test_a_success_resets_the_counter(
    session: AsyncSession, ctx: dict[str, Any], consumer: ConsumerFactory
) -> None:
    consumer(httpx.Response(200))
    endpoint, delivery = await _seed(session, consecutive_failures=3)

    await deliver_webhook(ctx, delivery_id=str(delivery.id))

    session.expunge_all()
    refreshed = await session.get(WebhookEndpoint, endpoint.id)
    assert refreshed is not None
    assert refreshed.consecutive_failures == 0


async def test_a_disabled_endpoint_is_not_called_at_all(
    session: AsyncSession, ctx: dict[str, Any], consumer: ConsumerFactory
) -> None:
    fake = consumer(httpx.Response(200))
    _, delivery = await _seed(session, enabled=False)

    await deliver_webhook(ctx, delivery_id=str(delivery.id))

    assert fake.requests == []
    assert (await _reload(session, delivery.id)).status == "dead"


# --- SSRF at delivery time ---------------------------------------------------


async def test_a_hostname_that_rebinds_to_a_private_ip_is_never_requested(
    session: AsyncSession,
    ctx: dict[str, Any],
    consumer: ConsumerFactory,
    enqueued: list[tuple[str, dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The case the second guard call exists for, and the only one a save-time
    check cannot catch.

    The endpoint's hostname passed the guard when it was saved. DNS belongs to
    whoever registered it, so by delivery time the *same string* resolves to
    10.0.0.5 -- and the delivery is dead immediately rather than retried five
    more times at an internal address.

    `app.core.url_guard.socket` is replaced rather than `socket.getaddrinfo`
    globally: this test holds a real Postgres connection, and a global patch
    answers asyncpg's own lookup with a fabricated `(ip, 0)` sockaddr.
    """
    fake = consumer(httpx.Response(200))
    _, delivery = await _seed(session)
    endpoint = (await session.execute(select(WebhookEndpoint))).scalars().first()
    assert endpoint is not None
    endpoint.url = "https://rebinding.internal.test/hook"
    await session.commit()

    def _rebinding_getaddrinfo(
        host: str, *_args: object, **_kwargs: object
    ) -> list[tuple[object, ...]]:
        if host != "rebinding.internal.test":
            raise socket.gaierror(socket.EAI_NONAME, "fake DNS: no such host")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]

    async def _rebinding(host: str) -> list[object]:
        return _rebinding_getaddrinfo(host)

    monkeypatch.setattr(url_guard, "_getaddrinfo", _rebinding)

    await deliver_webhook(ctx, delivery_id=str(delivery.id))

    assert fake.requests == []
    row = await _reload(session, delivery.id)
    assert row.status == "dead"
    assert "unsafe destination" in (row.error or "")
    assert enqueued == []


async def test_a_url_edited_to_the_metadata_address_is_never_requested(
    session: AsyncSession,
    ctx: dict[str, Any],
    consumer: ConsumerFactory,
    enqueued: list[tuple[str, dict[str, Any]]],
) -> None:
    """The blunter half: a literal address that never goes near DNS.

    Reachable only by editing the row directly -- the API rejects it at save
    time -- which is exactly the state a restored backup or a hand-edited
    table can be in.
    """
    fake = consumer(httpx.Response(200))
    _, delivery = await _seed(session)
    endpoint = (await session.execute(select(WebhookEndpoint))).scalars().first()
    assert endpoint is not None
    endpoint.url = "http://169.254.169.254/latest/meta-data/"
    await session.commit()

    await deliver_webhook(ctx, delivery_id=str(delivery.id))

    assert fake.requests == []
    assert (await _reload(session, delivery.id)).status == "dead"
    assert enqueued == []


# --- the module toggle -------------------------------------------------------


async def test_a_delivery_enqueued_before_the_module_was_switched_off_is_dropped(
    session: AsyncSession,
    ctx: dict[str, Any],
    consumer: ConsumerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = consumer(httpx.Response(200))
    _, delivery = await _seed(session)
    monkeypatch.setattr(webhooks_job, "is_enabled", lambda _name: False)

    await deliver_webhook(ctx, delivery_id=str(delivery.id))

    assert fake.requests == []
    assert (await _reload(session, delivery.id)).status == "pending"


# --- the sweep, the gauge and retention --------------------------------------


async def test_the_sweep_re_enqueues_a_delivery_whose_job_was_lost(
    session: AsyncSession,
    ctx: dict[str, Any],
    enqueued: list[tuple[str, dict[str, Any]]],
) -> None:
    _, delivery = await _seed(session)
    await _make_due(session, delivery.id)

    await sweep_due_webhook_deliveries(ctx)

    assert ("deliver_webhook", {"delivery_id": str(delivery.id)}) in enqueued


async def test_the_sweep_publishes_the_queue_depth_for_the_api_process(
    session: AsyncSession, ctx: dict[str, Any], redis: Redis
) -> None:
    """The gauge has to be readable from the process that serves /metrics,
    which is not this one -- hence Redis rather than a process-local
    registry (the same split forge_active_sessions lives with)."""
    _, delivery = await _seed(session)
    await _make_due(session, delivery.id)

    await sweep_due_webhook_deliveries(ctx)

    published = await redis.get(WEBHOOK_QUEUE_DEPTH_REDIS_KEY)
    assert published is not None
    assert int(published) >= 1


async def test_retention_deletes_old_deliveries_and_leaves_recent_ones(
    session: AsyncSession, ctx: dict[str, Any]
) -> None:
    endpoint, recent = await _seed(session)
    old = await _extra_delivery(session, endpoint)
    old.created_at = utcnow() - timedelta(days=400)
    await session.commit()

    await prune_webhook_deliveries(ctx)

    session.expunge_all()
    assert await session.get(WebhookDelivery, old.id) is None
    assert await session.get(WebhookDelivery, recent.id) is not None


# --- helpers -----------------------------------------------------------------


async def _make_due(session: AsyncSession, delivery_id: UUID) -> None:
    """Moves the row's next attempt into the past instead of sleeping."""
    session.expunge_all()
    row = await session.get(WebhookDelivery, delivery_id)
    assert row is not None
    row.next_attempt_at = utcnow() - timedelta(seconds=1)
    await session.commit()


async def _add_owner(session: AsyncSession, org_id: UUID) -> User:
    user = User(
        email=f"owner-{uuid4().hex[:8]}@example.test",
        hashed_password="x",
        email_verified_at=utcnow(),
    )
    session.add(user)
    await session.flush()
    session.add(Membership(user_id=user.id, org_id=org_id, role=MemberRole.OWNER))
    await session.commit()
    return user


async def _extra_delivery(session: AsyncSession, endpoint: WebhookEndpoint) -> WebhookDelivery:
    delivery = WebhookDelivery(
        org_id=endpoint.org_id,
        endpoint_id=endpoint.id,
        event_type="member.invited",
        payload={"id": str(uuid4()), "type": "member.invited", "data": {}},
        status="pending",
        next_attempt_at=utcnow(),
        delivery_id=uuid4(),
    )
    session.add(delivery)
    await session.commit()
    return delivery


# --- review R6: a receiver cannot occupy the worker with its response ----------


class _EndlessBody(httpx.AsyncByteStream):
    """A response body that never ends, counting how much of it was read."""

    def __init__(self, *, pause: float = 0.0, chunk: bytes = b"x" * 65536) -> None:
        self.chunks_read = 0
        self._pause = pause
        self._chunk = chunk

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while True:
            if self._pause:
                await asyncio.sleep(self._pause)
            self.chunks_read += 1
            yield self._chunk


async def test_a_huge_response_body_is_read_only_up_to_the_cap(
    session: AsyncSession, ctx: dict[str, Any], consumer: ConsumerFactory
) -> None:
    """`client.post()` buffered the whole body before slicing it. A receiver
    answering with an endless body now costs one chunk, not the worker."""
    body = _EndlessBody()
    consumer(httpx.Response(200, stream=body))
    _, delivery = await _seed(session)

    await deliver_webhook(ctx, delivery_id=str(delivery.id))

    row = await _reload(session, delivery.id)
    assert row.status == "delivered"
    assert row.response_body is not None
    assert len(row.response_body) == MAX_RESPONSE_BODY_CHARS
    assert body.chunks_read == 1


async def test_a_trickling_response_ends_at_the_deadline(
    session: AsyncSession,
    ctx: dict[str, Any],
    consumer: ConsumerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A byte at a time never trips a per-read timeout. The overall deadline
    ends it, and it is an ordinary failed attempt, retried on the ladder."""
    monkeypatch.setenv("WEBHOOK_DEADLINE_SECONDS", "0.5")
    get_settings.cache_clear()
    consumer(httpx.Response(200, stream=_EndlessBody(pause=0.1, chunk=b"x")))
    _, delivery = await _seed(session)

    started = time.monotonic()
    await deliver_webhook(ctx, delivery_id=str(delivery.id))

    assert time.monotonic() - started < 5
    row = await _reload(session, delivery.id)
    assert row.status == "pending"
    assert "no complete response within 0.5s" in (row.error or "")


async def test_a_slow_name_lookup_counts_against_the_same_deadline(
    session: AsyncSession,
    ctx: dict[str, Any],
    consumer: ConsumerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WEBHOOK_DEADLINE_SECONDS", "0.3")
    get_settings.cache_clear()
    fake = consumer(httpx.Response(200))
    _, delivery = await _seed(session)
    endpoint = (await session.execute(select(WebhookEndpoint))).scalars().first()
    assert endpoint is not None
    endpoint.url = "https://slow-dns.example.test/hook"
    await session.commit()

    async def _slow(host: str) -> list[object]:  # noqa: ARG001 -- the resolver's signature
        await asyncio.sleep(5)
        return []

    monkeypatch.setattr(url_guard, "_getaddrinfo", _slow)

    await deliver_webhook(ctx, delivery_id=str(delivery.id))

    assert fake.requests == []
    row = await _reload(session, delivery.id)
    assert row.status == "pending"
    assert "no complete response" in (row.error or "")


async def test_no_database_connection_is_held_during_the_request(
    session: AsyncSession,
    test_engine: AsyncEngine,
    ctx: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under pgbouncer transaction pooling an open transaction pins a server
    connection; the job used to keep one open for the whole outbound call."""
    _, delivery = await _seed(session)
    await session.close()
    checked_out: list[int] = []
    real_client = httpx.AsyncClient

    def _handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001 -- MockTransport's signature
        checked_out.append(test_engine.pool.checkedout())
        return httpx.Response(200, stream=httpx.ByteStream(b""))

    def _client(**kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(_handler)
        return real_client(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(webhooks_job.httpx, "AsyncClient", _client)

    await deliver_webhook(ctx, delivery_id=str(delivery.id))

    assert checked_out == [0]
