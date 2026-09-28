"""
Verifies T-103's REST surface for the notifications module: listing (org +
user scoped, with unread_only filtering), mark-read/mark-all-read (both
scoped to the caller's own notifications -- a notification belonging to
another user in the same org is indistinguishable from a missing one),
unread-count (and that mark-read invalidates its Redis cache), and editing
delivery preferences via PATCH /api/v1/me/notification-preferences.

Same real-Postgres + real-Redis testcontainers / httpx.AsyncClient(
ASGITransport(...)) pattern as test_gdpr_router.py / test_billing_router.py
-- see test_tenancy_router.py's docstring for why TestClient is avoided.
"""

from collections.abc import AsyncGenerator, Callable, Iterator
from typing import Any
from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport
from limits.storage import RedisStorage, storage_from_string
from limits.strategies import FixedWindowRateLimiter
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel
from testcontainers.redis import RedisContainer

from app.core.db import get_session
from app.core.dependencies import get_redis
from app.main import app
from app.middleware import rate_limit as rate_limit_module
from app.models._mixins import utcnow
from app.modules.auth.models import User
from app.modules.auth.permissions import MemberRole
from app.modules.auth.utils import create_access_token
from app.modules.billing.models import Plan
from app.modules.notifications.models import Notification, PushToken
from app.modules.tenancy.models import Membership, Organization
from tests._database import DatabaseHandle

# T-291: this file needs notifications enabled -- its routes, its tables, or
# both. CI's module-matrix job deselects it in the combination that turns
# every optional module off.
pytestmark = pytest.mark.module_dependent

# Plan is here only because Organization.plan_id declares a real FK to it
# (see app.modules.tenancy.models) -- creating Organization's table without
# Plan's also existing fails with "relation 'plan' does not exist", same as
# test_billing_router.py / test_notification_service.py's _TABLES lists.
_TABLES = [
    User.__table__,
    Plan.__table__,
    Organization.__table__,
    Membership.__table__,
    Notification.__table__,
    PushToken.__table__,
]


@pytest.fixture(scope="module")
def redis_container() -> "RedisContainer":
    with RedisContainer("redis:7-alpine") as container:
        yield container


# Function-scoped (not module-scoped): see test_auth_repository.py -- an
# asyncpg-backed engine can't be reused across pytest-asyncio's per-test loop.
@pytest.fixture
async def test_engine(postgres_container: DatabaseHandle) -> AsyncGenerator[AsyncEngine, None]:
    url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all, tables=_TABLES)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all, tables=_TABLES)
    await engine.dispose()


@pytest.fixture
def session_factory(test_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as s:
        yield s


@pytest.fixture
def get_session_override(
    session_factory: async_sessionmaker[AsyncSession],
) -> Callable[[], AsyncGenerator[AsyncSession, None]]:
    async def _get_session() -> AsyncGenerator[AsyncSession, None]:
        request_session = session_factory()
        try:
            yield request_session
        except Exception:
            await request_session.rollback()
            raise
        finally:
            await request_session.close()

    return _get_session


@pytest.fixture
async def redis_client(redis_container: "RedisContainer") -> AsyncGenerator[Redis, None]:
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    client: Redis = Redis.from_url(f"redis://{host}:{port}/0")
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture(scope="module")
def _redis_backed_storage(redis_container: "RedisContainer") -> Iterator[RedisStorage]:
    # Same swap-the-singleton's-storage approach as test_gdpr_router.py: the
    # api_limit decorator closes over the one `limiter` object built at
    # import time, so pointing its storage at a disposable Redis (and
    # restoring it after) is how requests against it actually get counted.
    original_storage = rate_limit_module.limiter._storage
    original_limiter = rate_limit_module.limiter._limiter
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    storage = storage_from_string(f"redis://{host}:{port}/1")
    rate_limit_module.limiter._storage = storage
    rate_limit_module.limiter._limiter = FixedWindowRateLimiter(storage)
    yield storage
    rate_limit_module.limiter._storage = original_storage
    rate_limit_module.limiter._limiter = original_limiter


@pytest.fixture
async def client(
    _redis_backed_storage: RedisStorage,
    get_session_override: Callable[[], AsyncGenerator[AsyncSession, None]],
    redis_client: Redis,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    _redis_backed_storage.reset()
    app.dependency_overrides[get_session] = get_session_override
    app.dependency_overrides[get_redis] = lambda: redis_client
    try:
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as c:
                yield c
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
async def owner(session: AsyncSession) -> User:
    u = User(email=f"owner-{uuid4()}@example.com")
    session.add(u)
    await session.flush()
    await session.commit()
    return u


@pytest.fixture
async def org(session: AsyncSession, owner: User) -> Organization:
    organization = Organization(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    session.add(organization)
    await session.flush()
    session.add(Membership(user_id=owner.id, org_id=organization.id, role=MemberRole.OWNER))
    await session.commit()
    return organization


async def _add_member(
    session: AsyncSession, org: Organization, *, role: MemberRole = MemberRole.MEMBER
) -> User:
    user = User(email=f"member-{uuid4()}@example.com")
    session.add(user)
    await session.flush()
    session.add(Membership(user_id=user.id, org_id=org.id, role=role))
    await session.commit()
    return user


async def _make_notification(
    session: AsyncSession,
    org: Organization,
    user: User,
    *,
    notification_type: str = "member_invited",
    read: bool = False,
) -> Notification:
    notification = Notification(
        org_id=org.id,
        user_id=user.id,
        type=notification_type,
        # What NotificationService.send() writes: the send-time rendering of
        # this type's catalog entry plus the params that produced it (T-234).
        title="Invitation sent",
        body="You invited new@example.com to join Acme.",
        params={"email": "new@example.com", "org_name": "Acme"},
        channels=["in_app"],
    )
    session.add(notification)
    await session.flush()
    if read:
        notification.read_at = utcnow()
        await session.flush()
    await session.commit()
    return notification


def _headers(user: User, org: Organization) -> dict[str, str]:
    token = create_access_token(user.id, None, ())
    return {"Authorization": f"Bearer {token}", "X-Org-ID": str(org.id)}


# --- GET /notifications ---------------------------------------------------------------


async def test_list_notifications_requires_auth(
    client: httpx.AsyncClient, org: Organization
) -> None:
    response = await client.get("/api/v1/notifications", headers={"X-Org-ID": str(org.id)})

    assert response.status_code == 401


async def test_list_notifications_requires_org_header(
    client: httpx.AsyncClient, owner: User
) -> None:
    token = create_access_token(owner.id, None, ())
    response = await client.get(
        "/api/v1/notifications", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 400


async def test_list_notifications_returns_only_callers_own(
    client: httpx.AsyncClient, session: AsyncSession, owner: User, org: Organization
) -> None:
    member = await _add_member(session, org)
    await _make_notification(session, org, owner)
    await _make_notification(session, org, member)

    response = await client.get("/api/v1/notifications", headers=_headers(owner, org))

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["title"] == "Invitation sent"


async def test_list_notifications_renders_in_the_readers_current_locale(
    client: httpx.AsyncClient, session: AsyncSession, owner: User, org: Organization
) -> None:
    """T-234: the feed is re-rendered per read, not served as the strings
    frozen at send time -- switching language switches the whole history,
    rather than leaving it in whatever language was current back then."""
    await _make_notification(session, org, owner)

    owner.locale = "fi"
    await session.commit()

    response = await client.get("/api/v1/notifications", headers=_headers(owner, org))

    assert response.status_code == 200
    data = response.json()["data"]
    assert data[0]["title"] == "Kutsu lähetetty"
    assert "new@example.com" in data[0]["body"]


async def test_list_notifications_unread_only_filters_read_ones(
    client: httpx.AsyncClient, session: AsyncSession, owner: User, org: Organization
) -> None:
    await _make_notification(session, org, owner, read=True)
    unread = await _make_notification(session, org, owner, read=False)

    response = await client.get(
        "/api/v1/notifications", params={"unread_only": "true"}, headers=_headers(owner, org)
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert [n["id"] for n in data] == [str(unread.id)]


# --- PATCH /notifications/{id}/read ----------------------------------------------------


async def test_mark_notification_read_happy_path(
    client: httpx.AsyncClient, session: AsyncSession, owner: User, org: Organization
) -> None:
    notification = await _make_notification(session, org, owner)

    response = await client.patch(
        f"/api/v1/notifications/{notification.id}/read", headers=_headers(owner, org)
    )

    assert response.status_code == 200
    assert response.json()["data"]["read_at"] is not None


async def test_mark_notification_read_cross_user_same_org_returns_404(
    client: httpx.AsyncClient, session: AsyncSession, owner: User, org: Organization
) -> None:
    member = await _add_member(session, org)
    notification = await _make_notification(session, org, owner)

    response = await client.patch(
        f"/api/v1/notifications/{notification.id}/read", headers=_headers(member, org)
    )

    assert response.status_code == 404


async def test_mark_notification_read_unknown_id_returns_404(
    client: httpx.AsyncClient, owner: User, org: Organization
) -> None:
    response = await client.patch(
        f"/api/v1/notifications/{uuid4()}/read", headers=_headers(owner, org)
    )

    assert response.status_code == 404


async def test_mark_notification_read_invalidates_unread_count_cache(
    client: httpx.AsyncClient, session: AsyncSession, owner: User, org: Organization
) -> None:
    notification = await _make_notification(session, org, owner)

    before = await client.get("/api/v1/notifications/unread-count", headers=_headers(owner, org))
    assert before.json()["data"]["count"] == 1

    await client.patch(
        f"/api/v1/notifications/{notification.id}/read", headers=_headers(owner, org)
    )

    after = await client.get("/api/v1/notifications/unread-count", headers=_headers(owner, org))
    assert after.json()["data"]["count"] == 0


# --- PATCH /notifications/{id}/unread (F-009's undo) -----------------------------------


async def test_mark_notification_unread_restores_the_state(
    client: httpx.AsyncClient, session: AsyncSession, owner: User, org: Organization
) -> None:
    """The undo path: read, then unread, leaves the row exactly as it was.

    Asserted on read_at rather than only on the unread count, because
    "restores state, not just appearance" is the F-009 requirement -- a count
    that happens to line up would also pass a weaker test.
    """
    notification = await _make_notification(session, org, owner)
    await client.patch(
        f"/api/v1/notifications/{notification.id}/read", headers=_headers(owner, org)
    )

    response = await client.patch(
        f"/api/v1/notifications/{notification.id}/unread", headers=_headers(owner, org)
    )

    assert response.status_code == 200
    assert response.json()["data"]["read_at"] is None


async def test_mark_notification_unread_cross_user_same_org_returns_404(
    client: httpx.AsyncClient, session: AsyncSession, owner: User, org: Organization
) -> None:
    """An undo gets no more authority than the action it reverses."""
    member = await _add_member(session, org)
    notification = await _make_notification(session, org, owner)

    response = await client.patch(
        f"/api/v1/notifications/{notification.id}/unread", headers=_headers(member, org)
    )

    assert response.status_code == 404


async def test_mark_notification_unread_unknown_id_returns_404(
    client: httpx.AsyncClient, owner: User, org: Organization
) -> None:
    response = await client.patch(
        f"/api/v1/notifications/{uuid4()}/unread", headers=_headers(owner, org)
    )

    assert response.status_code == 404


async def test_mark_notification_unread_invalidates_unread_count_cache(
    client: httpx.AsyncClient, session: AsyncSession, owner: User, org: Organization
) -> None:
    """The bell's badge has to come back too -- the count is Redis-cached, and
    a stale zero after an undo is the visible symptom."""
    notification = await _make_notification(session, org, owner)
    await client.patch(
        f"/api/v1/notifications/{notification.id}/read", headers=_headers(owner, org)
    )
    read = await client.get("/api/v1/notifications/unread-count", headers=_headers(owner, org))
    assert read.json()["data"]["count"] == 0

    await client.patch(
        f"/api/v1/notifications/{notification.id}/unread", headers=_headers(owner, org)
    )

    after = await client.get("/api/v1/notifications/unread-count", headers=_headers(owner, org))
    assert after.json()["data"]["count"] == 1


# --- POST /notifications/read-all ------------------------------------------------------


async def test_mark_all_notifications_read_returns_updated_count(
    client: httpx.AsyncClient, session: AsyncSession, owner: User, org: Organization
) -> None:
    await _make_notification(session, org, owner)
    await _make_notification(session, org, owner)
    await _make_notification(session, org, owner, read=True)

    response = await client.post("/api/v1/notifications/read-all", headers=_headers(owner, org))

    assert response.status_code == 200
    assert response.json()["data"]["updated"] == 2

    listing = await client.get(
        "/api/v1/notifications", params={"unread_only": "true"}, headers=_headers(owner, org)
    )
    assert listing.json()["data"] == []


# --- GET /notifications/unread-count ---------------------------------------------------


async def test_unread_count_returns_correct_count(
    client: httpx.AsyncClient, session: AsyncSession, owner: User, org: Organization
) -> None:
    await _make_notification(session, org, owner)
    await _make_notification(session, org, owner, read=True)

    response = await client.get("/api/v1/notifications/unread-count", headers=_headers(owner, org))

    assert response.status_code == 200
    assert response.json()["data"]["count"] == 1


# --- PATCH /me/notification-preferences ------------------------------------------------


async def test_update_notification_preferences_requires_auth(client: httpx.AsyncClient) -> None:
    response = await client.patch(
        "/api/v1/me/notification-preferences",
        json={"preferences": {"member_invited": {"email": False}}},
    )

    assert response.status_code == 401


async def test_update_notification_preferences_unknown_type_returns_422(
    client: httpx.AsyncClient, owner: User
) -> None:
    token = create_access_token(owner.id, None, ())
    response = await client.patch(
        "/api/v1/me/notification-preferences",
        json={"preferences": {"not_a_real_type": {"email": False}}},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422


async def test_update_notification_preferences_happy_path_persists(
    client: httpx.AsyncClient, owner: User, session: AsyncSession
) -> None:
    token = create_access_token(owner.id, None, ())
    payload: dict[str, Any] = {"preferences": {"member_invited": {"email": False, "in_app": True}}}

    response = await client.patch(
        "/api/v1/me/notification-preferences",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["preferences"] == payload["preferences"]

    await session.refresh(owner)
    assert owner.notification_preferences == payload["preferences"]


async def test_update_notification_preferences_forces_a_required_channel_back_on(
    client: httpx.AsyncClient, owner: User, session: AsyncSession
) -> None:
    """T-234: the dunning email can't be switched off. Coerced rather than
    rejected, and echoed back coerced, so the settings page immediately shows
    the true state instead of a checkbox the send path ignores."""
    token = create_access_token(owner.id, None, ())

    response = await client.patch(
        "/api/v1/me/notification-preferences",
        json={"preferences": {"payment_failed": {"email": False, "in_app": False, "push": True}}},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    expected = {"payment_failed": {"email": True, "in_app": False, "push": True}}
    assert response.json()["data"]["preferences"] == expected

    await session.refresh(owner)
    assert owner.notification_preferences == expected


# --- POST /notifications/push-tokens ---------------------------------------------------


async def test_register_push_token_requires_auth(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/notifications/push-tokens",
        json={"token": "ExponentPushToken[abc]", "platform": "ios"},
    )

    assert response.status_code == 401


async def test_register_push_token_rejects_unknown_platform(
    client: httpx.AsyncClient, owner: User
) -> None:
    token = create_access_token(owner.id, None, ())
    response = await client.post(
        "/api/v1/notifications/push-tokens",
        json={"token": "ExponentPushToken[abc]", "platform": "windows-phone"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422


async def test_register_push_token_happy_path_persists(
    client: httpx.AsyncClient, owner: User, session: AsyncSession
) -> None:
    token = create_access_token(owner.id, None, ())
    response = await client.post(
        "/api/v1/notifications/push-tokens",
        json={
            "token": "ExponentPushToken[abc]",
            "platform": "ios",
            "device_label": "Jane's iPhone",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["platform"] == "ios"
    assert body["device_label"] == "Jane's iPhone"

    stored = (
        await session.execute(select(PushToken).where(PushToken.token == "ExponentPushToken[abc]"))
    ).scalar_one()
    assert stored.user_id == owner.id


async def test_register_push_token_reregistering_same_token_updates_not_duplicates(
    client: httpx.AsyncClient, owner: User, session: AsyncSession
) -> None:
    token = create_access_token(owner.id, None, ())
    shared_token = "ExponentPushToken[shared]"

    await client.post(
        "/api/v1/notifications/push-tokens",
        json={"token": shared_token, "platform": "android", "device_label": "Old device"},
        headers={"Authorization": f"Bearer {token}"},
    )
    response = await client.post(
        "/api/v1/notifications/push-tokens",
        json={"token": shared_token, "platform": "ios", "device_label": "New device"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    rows = (
        (await session.execute(select(PushToken).where(PushToken.token == shared_token)))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].platform == "ios"
    assert rows[0].device_label == "New device"
