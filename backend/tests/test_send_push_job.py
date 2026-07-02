"""
Verifies T-102's send_push ARQ job: a no-op (no error, no push_sent_at) when
the notification has vanished or the user has no active PushToken, posts one
Expo push message per active token on the happy path and sets push_sent_at,
deactivates a token Expo reports "DeviceNotRegistered" for, and never raises
on a transport failure (logged + reported to Sentry instead, same contract as
app.workers.jobs.email.send_email). Same real-Postgres testcontainers pattern
as test_gdpr_purge_job.py: the job's module-level async_session_factory is
monkeypatched to a session factory bound to the test engine, and httpx is
mocked rather than hitting Expo's real API.
"""

from collections.abc import AsyncGenerator
from typing import Any, Self
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel
from testcontainers.postgres import PostgresContainer

from app.modules.auth.models import User
from app.modules.billing.models import Plan
from app.modules.notifications.models import Notification, PushToken
from app.modules.notifications.repository import PushTokenRepository
from app.modules.tenancy.models import Organization
from app.workers.jobs import notifications as notifications_job_module
from app.workers.jobs.notifications import send_push

_TABLES = [
    User.__table__,
    Plan.__table__,
    Organization.__table__,
    Notification.__table__,
    PushToken.__table__,
]


class _FakeResponse:
    def __init__(self, receipts: list[dict[str, Any]]) -> None:
        self._receipts = receipts

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return {"data": self._receipts}


class _FakeAsyncClient:
    def __init__(
        self, receipts: list[dict[str, Any]], captured: list[list[dict[str, Any]]]
    ) -> None:
        self._receipts = receipts
        self._captured = captured

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def post(self, _url: str, json: list[dict[str, Any]]) -> _FakeResponse:
        self._captured.append(json)
        return _FakeResponse(self._receipts)


class _FailingAsyncClient:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def post(self, *_args: object, **_kwargs: object) -> _FakeResponse:
        raise httpx.ConnectError("boom")


def _mock_expo(
    monkeypatch: pytest.MonkeyPatch, receipts: list[dict[str, Any]]
) -> list[list[dict[str, Any]]]:
    captured: list[list[dict[str, Any]]] = []
    monkeypatch.setattr(
        notifications_job_module.httpx,
        "AsyncClient",
        lambda *_a, **_kw: _FakeAsyncClient(receipts, captured),
    )
    return captured


@pytest.fixture(scope="module")
def postgres_container() -> "PostgresContainer":
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


# Function-scoped (not module-scoped): see test_auth_repository.py -- an
# asyncpg-backed engine can't be reused across pytest-asyncio's per-test loop.
@pytest.fixture
async def test_engine(postgres_container: "PostgresContainer") -> AsyncGenerator[AsyncEngine, None]:
    url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all, tables=_TABLES)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all, tables=_TABLES)
    await engine.dispose()


@pytest.fixture(autouse=True)
def _use_test_engine(monkeypatch: pytest.MonkeyPatch, test_engine: AsyncEngine) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(notifications_job_module, "async_session_factory", factory)


@pytest.fixture
def session_factory(test_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as s:
        yield s


async def _make_user_and_org(session: AsyncSession) -> tuple[User, Organization]:
    user = User(email=f"user-{uuid4()}@example.com")
    org = Organization(name="Acme", slug=f"acme-{uuid4()}")
    session.add_all([user, org])
    await session.flush()
    await session.commit()
    return user, org


async def _make_notification(session: AsyncSession, user: User, org: Organization) -> Notification:
    notification = Notification(
        org_id=org.id,
        user_id=user.id,
        type="payment_failed",
        title="Payment failed",
        body="Update your card",
        action_url="/billing",
        channels=["push"],
    )
    session.add(notification)
    await session.flush()
    await session.commit()
    return notification


async def test_noop_when_notification_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _mock_expo(monkeypatch, [])

    await send_push({}, user_id=uuid4(), notification_id=uuid4())

    assert captured == []


async def test_noop_when_user_has_no_active_tokens(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user, org = await _make_user_and_org(session)
    notification = await _make_notification(session, user, org)
    captured = _mock_expo(monkeypatch, [])

    await send_push({}, user_id=user.id, notification_id=notification.id)

    assert captured == []
    async with session_factory() as verify_session:
        refreshed = await verify_session.get(Notification, notification.id)
        assert refreshed is not None
        assert refreshed.push_sent_at is None


async def test_happy_path_posts_message_per_token_and_sets_push_sent_at(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user, org = await _make_user_and_org(session)
    notification = await _make_notification(session, user, org)
    await PushTokenRepository(session).register(user.id, "ExponentPushToken[a]", "ios")
    await session.commit()
    captured = _mock_expo(monkeypatch, [{"status": "ok"}])

    await send_push({}, user_id=user.id, notification_id=notification.id)

    assert len(captured) == 1
    assert captured[0] == [
        {
            "to": "ExponentPushToken[a]",
            "title": "Payment failed",
            "body": "Update your card",
            "data": {"action_url": "/billing"},
        }
    ]
    async with session_factory() as verify_session:
        refreshed = await verify_session.get(Notification, notification.id)
        assert refreshed is not None
        assert refreshed.push_sent_at is not None


async def test_device_not_registered_receipt_deactivates_token(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user, org = await _make_user_and_org(session)
    notification = await _make_notification(session, user, org)
    token = await PushTokenRepository(session).register(user.id, "ExponentPushToken[b]", "ios")
    await session.commit()
    _mock_expo(
        monkeypatch,
        [{"status": "error", "details": {"error": "DeviceNotRegistered"}}],
    )

    await send_push({}, user_id=user.id, notification_id=notification.id)

    async with session_factory() as verify_session:
        refreshed = await verify_session.get(PushToken, token.id)
        assert refreshed is not None
        assert refreshed.deleted_at is not None


async def test_other_error_receipt_does_not_deactivate_token(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user, org = await _make_user_and_org(session)
    notification = await _make_notification(session, user, org)
    token = await PushTokenRepository(session).register(user.id, "ExponentPushToken[c]", "ios")
    await session.commit()
    _mock_expo(
        monkeypatch,
        [{"status": "error", "details": {"error": "MessageTooBig"}}],
    )

    await send_push({}, user_id=user.id, notification_id=notification.id)

    async with session_factory() as verify_session:
        refreshed = await verify_session.get(PushToken, token.id)
        assert refreshed is not None
        assert refreshed.deleted_at is None


async def test_transport_failure_does_not_raise_and_leaves_push_sent_at_unset(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user, org = await _make_user_and_org(session)
    notification = await _make_notification(session, user, org)
    await PushTokenRepository(session).register(user.id, "ExponentPushToken[d]", "ios")
    await session.commit()
    monkeypatch.setattr(
        notifications_job_module.httpx, "AsyncClient", lambda *_a, **_kw: _FailingAsyncClient()
    )
    captured_sentry: list[Exception] = []
    monkeypatch.setattr(
        notifications_job_module.sentry_sdk, "capture_exception", captured_sentry.append
    )

    await send_push({}, user_id=user.id, notification_id=notification.id)

    assert len(captured_sentry) == 1
    async with session_factory() as verify_session:
        refreshed = await verify_session.get(Notification, notification.id)
        assert refreshed is not None
        assert refreshed.push_sent_at is None
