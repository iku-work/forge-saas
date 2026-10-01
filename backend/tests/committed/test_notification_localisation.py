"""
T-234: proves -- rather than assumes -- that a notification reaches each
recipient in that recipient's own language, along the whole chain from one
event to the Expo push payload.

The chain under test is real end to end, with only the two network edges
faked: NotificationService.send_many() writes real rows to a real Postgres
and publishes to a real Redis, its `send_push` enqueue is captured by
conftest.py's autouse `enqueued_jobs` fixture, and that captured kwargs dict
is then handed straight to the actual send_push job, whose HTTP call to
Expo is intercepted so the assertions can be made on the outbound payload
itself (the "capture outbound Expo API calls (mock transport)" fixture T-234
asks for).

The bugs this file exists to catch, all found by T-234's audit of the T-068 /
T-102 / T-105 chain and fixed alongside it:

- every call site built an English `title=`/`body=` literal itself, so a
  Finnish recipient got English on every channel;
- `send_push` posted those stored strings verbatim, so a locale change
  between a notification being created and its (possibly days-deferred) push
  job running had no effect;
- the email channel enqueued `template=<notification type>` with a
  `{title, body, action_url}` context no template of that name accepted --
  `file_infected` had no template at all and failed the job outright;
- the push payload carried no `locale`, leaving the mobile client to guess
  which language the strings it just received were in.
"""

from typing import Any, Self
from uuid import UUID, uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from app.modules.auth.models import User
from app.modules.notifications import messages
from app.modules.notifications.models import Notification
from app.modules.notifications.repository import PushTokenRepository
from app.modules.notifications.service import NotificationService
from app.modules.tenancy.models import Organization
from app.workers.jobs import notifications as notifications_job_module
from app.workers.jobs.notifications import send_push

# The `payment_failed` params every test here sends: locale-independent data
# (an org name, a formatted amount), which is the only kind that may be
# computed once for a whole fan-out.
_PAYMENT_FAILED_PARAMS = {"org_name": "Acme", "amount": "$49.00"}

_EN_TITLE = "Payment failed"
_FI_TITLE = "Maksu epäonnistui"


@pytest.fixture(autouse=True)
def _use_test_engine(
    monkeypatch: pytest.MonkeyPatch, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    # send_push opens its own session rather than taking one -- same
    # monkeypatch as test_send_push_job.py.
    monkeypatch.setattr(notifications_job_module, "async_session_factory", session_factory)


@pytest.fixture
def service(session: AsyncSession, redis_client: Redis) -> NotificationService:
    return NotificationService(session, redis_client)


@pytest.fixture
async def org(session: AsyncSession) -> Organization:
    organization = Organization(name="Acme", slug=f"acme-{uuid4()}")
    session.add(organization)
    await session.flush()
    await session.commit()
    return organization


# --- Expo transport capture ------------------------------------------------------


class _FakeResponse:
    def __init__(self, receipts: list[dict[str, Any]]) -> None:
        self._receipts = receipts

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return {"data": self._receipts}


class _FakeAsyncClient:
    def __init__(self, captured: list[dict[str, Any]]) -> None:
        self._captured = captured

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def post(self, _url: str, json: list[dict[str, Any]]) -> _FakeResponse:
        self._captured.extend(json)
        return _FakeResponse([{"status": "ok"} for _ in json])


@pytest.fixture
def expo_messages(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Every message body posted to Expo's push API during a test."""
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(
        notifications_job_module.httpx,
        "AsyncClient",
        lambda *_a, **_kw: _FakeAsyncClient(captured),
    )
    return captured


async def _make_user(session: AsyncSession, locale: str) -> User:
    user = User(email=f"user-{uuid4()}@example.com", full_name="Sam", locale=locale)
    session.add(user)
    await session.flush()
    await session.commit()
    await PushTokenRepository(session).register(user.id, f"ExponentPushToken[{uuid4()}]", "ios")
    await session.commit()
    return user


async def _run_push_jobs(enqueued_jobs: list[tuple[str, dict[str, Any]]]) -> None:
    """Runs the send_push jobs NotificationService actually enqueued, with
    the exact kwargs it passed -- not a hand-written call."""
    for function, kwargs in enqueued_jobs:
        if function == "send_push":
            await send_push(
                {}, user_id=kwargs["user_id"], notification_id=kwargs["notification_id"]
            )


def _push_titles(expo_messages: list[dict[str, Any]]) -> list[str]:
    return sorted(message["title"] for message in expo_messages)


# --- the mixed-locale fan-out --------------------------------------------------------


async def test_one_event_reaches_two_recipients_each_in_their_own_language(
    service: NotificationService,
    session: AsyncSession,
    org: Organization,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
    expo_messages: list[dict[str, Any]],
) -> None:
    user_a = await _make_user(session, "en")
    user_b = await _make_user(session, "fi")

    await service.send_many(
        user_ids=[user_a.id, user_b.id],
        org_id=org.id,
        type="payment_failed",
        params=_PAYMENT_FAILED_PARAMS,
        action_url="/settings/billing",
    )
    await session.commit()
    await _run_push_jobs(enqueued_jobs)

    assert _push_titles(expo_messages) == [_FI_TITLE, _EN_TITLE]
    # ...and the bodies interpolate the same event's params into each language.
    bodies = {message["data"]["locale"]: message["body"] for message in expo_messages}
    assert bodies["en"].startswith("We couldn't charge your card for Acme's subscription ($49.00)")
    assert bodies["fi"].startswith("Emme voineet veloittaa korttiasi organisaation Acme")


async def test_push_payload_carries_the_recipients_locale(
    service: NotificationService,
    session: AsyncSession,
    org: Organization,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
    expo_messages: list[dict[str, Any]],
) -> None:
    user = await _make_user(session, "de")

    await service.send(
        user_id=user.id,
        org_id=org.id,
        type="payment_failed",
        params=_PAYMENT_FAILED_PARAMS,
        action_url="/settings/billing",
    )
    await session.commit()
    await _run_push_jobs(enqueued_jobs)

    assert len(expo_messages) == 1
    assert expo_messages[0]["data"] == {"action_url": "/settings/billing", "locale": "de"}


# --- fallbacks -----------------------------------------------------------------------


async def test_unsupported_stored_locale_falls_back_to_english_not_raw_keys(
    service: NotificationService,
    session: AsyncSession,
    org: Organization,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
    expo_messages: list[dict[str, Any]],
) -> None:
    # A locale this deployment has never enabled -- e.g. one turned off in
    # product.config after the user had already picked it.
    user = await _make_user(session, "xx")

    await service.send(
        user_id=user.id, org_id=org.id, type="payment_failed", params=_PAYMENT_FAILED_PARAMS
    )
    await session.commit()
    await _run_push_jobs(enqueued_jobs)

    assert expo_messages[0]["title"] == _EN_TITLE
    assert "notifications.types" not in expo_messages[0]["body"]


async def test_locale_changed_after_the_event_uses_the_locale_at_send_time(  # noqa: PLR0913 -- both sessions, both capture fixtures, and the service
    service: NotificationService,
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    org: Organization,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
    expo_messages: list[dict[str, Any]],
) -> None:
    user = await _make_user(session, "en")
    notification = await service.send(
        user_id=user.id, org_id=org.id, type="payment_failed", params=_PAYMENT_FAILED_PARAMS
    )
    await session.commit()
    assert notification.title == _EN_TITLE  # rendered when the row was written

    # The push job can run long after that -- day-7 dunning is a week later.
    async with session_factory() as switch_session:
        switching = await switch_session.get(User, user.id)
        assert switching is not None
        switching.locale = "fi"
        await switch_session.commit()

    await _run_push_jobs(enqueued_jobs)

    assert expo_messages[0]["title"] == _FI_TITLE
    assert expo_messages[0]["data"]["locale"] == "fi"


async def test_push_falls_back_to_stored_strings_for_a_type_with_no_catalog_entry(
    session: AsyncSession,
    org: Organization,
    expo_messages: list[dict[str, Any]],
) -> None:
    # What a row written before T-234's migration looks like: params `{}`, an
    # unknown type, and the English literal its caller baked in.
    user = await _make_user(session, "fi")
    notification = Notification(
        org_id=org.id,
        user_id=user.id,
        type="legacy_type_with_no_catalog_entry",
        title="Something happened",
        body="Have a look.",
        channels=["push"],
    )
    session.add(notification)
    await session.commit()

    await send_push({}, user_id=user.id, notification_id=notification.id)

    assert expo_messages[0]["title"] == "Something happened"
    assert expo_messages[0]["body"] == "Have a look."


# --- the other two channels (regression net around T-102) ----------------------------


async def test_email_channel_is_localised_per_recipient(
    service: NotificationService,
    session: AsyncSession,
    org: Organization,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    user_a = await _make_user(session, "en")
    user_b = await _make_user(session, "fi")

    await service.send_many(
        user_ids=[user_a.id, user_b.id],
        org_id=org.id,
        type="payment_failed",
        params=_PAYMENT_FAILED_PARAMS,
        action_url="/settings/billing",
    )
    await session.commit()

    emails = [kwargs for function, kwargs in enqueued_jobs if function == "send_email"]
    assert sorted(kwargs["locale"] for kwargs in emails) == ["en", "fi"]
    by_locale = {kwargs["locale"]: kwargs for kwargs in emails}
    # payment_failed has a rich template of its own; params are its props and
    # the CTA link carries the recipient's own locale segment (T-233).
    assert by_locale["fi"]["template"] == "payment_failed"
    assert by_locale["fi"]["context"]["amount"] == "$49.00"
    assert by_locale["fi"]["context"]["billing_url"].endswith("/fi/settings/billing")
    assert by_locale["en"]["context"]["billing_url"].endswith("/en/settings/billing")


async def test_email_channel_uses_the_generic_template_for_types_without_a_rich_one(
    service: NotificationService,
    session: AsyncSession,
    org: Organization,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    """file_infected used to enqueue `template="file_infected"`, which the
    Node renderer rejects outright -- no such template exists -- failing the
    job instead of sending anything."""
    user = await _make_user(session, "fi")

    await service.send(
        user_id=user.id, org_id=org.id, type="file_infected", params={"filename": "report.pdf"}
    )
    await session.commit()

    email = next(kwargs for function, kwargs in enqueued_jobs if function == "send_email")
    assert email["template"] == messages.GENERIC_EMAIL_TEMPLATE
    assert email["locale"] == "fi"
    # The generic template renders strings rather than looking them up, so it
    # receives the already-localised pair -- and the subject is that title.
    assert email["context"]["title"] == "Lataamasi tiedosto merkittiin saastuneeksi"
    assert email["subject"] == email["context"]["title"]
    assert "report.pdf" in email["context"]["body"]


async def test_in_app_channel_publishes_the_recipients_language(
    service: NotificationService,
    session: AsyncSession,
    org: Organization,
    redis_client: Redis,
) -> None:
    user = await _make_user(session, "fi")
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(f"notif:user:{user.id}")
    try:
        await pubsub.get_message(timeout=1)  # subscribe confirmation

        await service.send(
            user_id=user.id, org_id=org.id, type="payment_failed", params=_PAYMENT_FAILED_PARAMS
        )

        message = await pubsub.get_message(timeout=2)
        assert message is not None
        assert _FI_TITLE.encode() in message["data"]
    finally:
        await pubsub.unsubscribe(f"notif:user:{user.id}")
        await pubsub.aclose()


# --- idempotency across a fan-out ----------------------------------------------------


async def test_send_many_shares_one_idempotency_key_across_recipients(
    service: NotificationService,
    session: AsyncSession,
    org: Organization,
) -> None:
    """A redelivered webhook re-runs the whole fan-out; the key is scoped per
    (org, user) so it dedups each recipient's own row rather than letting the
    first recipient's row suppress everyone else's."""
    user_a = await _make_user(session, "en")
    user_b = await _make_user(session, "fi")
    ids: list[UUID] = [user_a.id, user_b.id]

    first = await service.send_many(
        user_ids=ids,
        org_id=org.id,
        type="payment_failed",
        params=_PAYMENT_FAILED_PARAMS,
        idempotency_key="evt_1",
    )
    second = await service.send_many(
        user_ids=ids,
        org_id=org.id,
        type="payment_failed",
        params=_PAYMENT_FAILED_PARAMS,
        idempotency_key="evt_1",
    )

    assert [n.id for n in first] == [n.id for n in second]
    assert len({n.id for n in first}) == 2
