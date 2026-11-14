"""
Verifies T-102's NotificationRepository and PushTokenRepository.

NotificationRepository is one recipient's inbox: another user's notification
is treated as missing, and so is one from an org the user is no longer a
member of (or that was deleted) -- on read, list, count and mark-all-read
alike. Also cursor pagination via list_page, the unread-count Redis cache
(including that a cache hit skips the DB entirely), the idempotency-key lookup
window, and PushToken's upsert-by-token + soft-delete-by-deactivate behaviour.

Same real-Postgres + real-Redis testcontainers pattern as test_dependencies.py
since unread_count needs a real cache, not a session-scoped fixture
NotificationService's own tests can share.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy import delete, event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
)

from app.core.exceptions import InvalidCursorError, NotFoundError
from app.models._mixins import utcnow
from app.modules.auth.models import User
from app.modules.auth.permissions import MemberRole
from app.modules.notifications.models import Notification
from app.modules.notifications.repository import NotificationRepository, PushTokenRepository
from app.modules.tenancy.models import Membership, Organization


async def _org(session: AsyncSession, name: str, *members: User) -> Organization:
    organization = Organization(name=name, slug=f"{name.lower()}-{uuid4()}")
    session.add(organization)
    await session.flush()
    for member in members:
        session.add(Membership(user_id=member.id, org_id=organization.id, role=MemberRole.MEMBER))
    await session.flush()
    return organization


@pytest.fixture
async def user(session: AsyncSession) -> User:
    u = User(email=f"user-{uuid4()}@example.com")
    session.add(u)
    await session.flush()
    return u


@pytest.fixture
async def other_user(session: AsyncSession) -> User:
    u = User(email=f"other-{uuid4()}@example.com")
    session.add(u)
    await session.flush()
    return u


@pytest.fixture
async def org(session: AsyncSession, user: User, other_user: User) -> Organization:
    return await _org(session, "Acme", user, other_user)


@pytest.fixture
def repo(session: AsyncSession, user: User, redis_client: Redis) -> NotificationRepository:
    return NotificationRepository(session, user.id, redis_client)


@pytest.fixture
def other_repo(
    session: AsyncSession, other_user: User, redis_client: Redis
) -> NotificationRepository:
    return NotificationRepository(session, other_user.id, redis_client)


async def _send(
    repo: NotificationRepository,
    org_id: UUID | None,
    *,
    type_: str = "member_invited",
    **extra: Any,  # noqa: ANN401 -- passed straight through to create()
) -> Notification:
    return await repo.create(
        org_id=org_id, type=type_, title="Title", body="Body", channels=["in_app"], **extra
    )


async def _leave(session: AsyncSession, user: User, org: Organization) -> None:
    m = cast("Any", Membership)
    await session.execute(delete(Membership).where(m.user_id == user.id, m.org_id == org.id))
    await session.flush()


# --- create / get_or_raise ---------------------------------------------------


async def test_create_sets_the_recipient(repo: NotificationRepository, user: User) -> None:
    notification = await _send(repo, None)

    assert notification.user_id == user.id
    assert notification.org_id is None


async def test_create_rejects_another_recipient(repo: NotificationRepository) -> None:
    with pytest.raises(ValueError, match="user_id"):
        await _send(repo, None, user_id=uuid4())


async def test_another_users_notification_is_not_found(
    repo: NotificationRepository, other_repo: NotificationRepository, org: Organization
) -> None:
    notification = await _send(repo, org.id)

    with pytest.raises(NotFoundError):
        await other_repo.get_or_raise(notification.id)
    with pytest.raises(NotFoundError):
        await other_repo.mark_read(notification.id)
    with pytest.raises(NotFoundError):
        await other_repo.mark_unread(notification.id)


# --- one inbox across orgs ----------------------------------------------------


async def test_the_inbox_spans_every_org_and_personal_items(
    session: AsyncSession, repo: NotificationRepository, user: User, org: Organization
) -> None:
    second_org = await _org(session, "Globex", user)
    in_first = await _send(repo, org.id)
    in_second = await _send(repo, second_org.id)
    personal = await _send(repo, None)

    items, _ = await repo.list_page()

    assert {n.id for n in items} == {in_first.id, in_second.id, personal.id}
    assert await repo.unread_count() == 3


async def test_leaving_an_org_hides_its_notifications(
    session: AsyncSession, repo: NotificationRepository, user: User, org: Organization
) -> None:
    left_behind = await _send(repo, org.id)
    personal = await _send(repo, None)
    await _leave(session, user, org)

    items, _ = await repo.list_page()
    assert [n.id for n in items] == [personal.id]
    assert await repo.unread_count() == 1
    with pytest.raises(NotFoundError):
        await repo.mark_read(left_behind.id)
    assert await repo.mark_all_read() == 1
    await session.refresh(left_behind)
    assert left_behind.read_at is None


async def test_a_deleted_orgs_notifications_are_hidden(
    session: AsyncSession, repo: NotificationRepository, org: Organization
) -> None:
    notification = await _send(repo, org.id)
    org.deleted_at = utcnow()
    await session.flush()

    items, _ = await repo.list_page()

    assert items == []
    with pytest.raises(NotFoundError):
        await repo.get_or_raise(notification.id)


async def test_a_cursor_from_an_org_the_user_left_is_refused(
    session: AsyncSession, repo: NotificationRepository, user: User, org: Organization
) -> None:
    anchor = await _send(repo, org.id)
    await _leave(session, user, org)

    with pytest.raises(InvalidCursorError):
        await repo.list_page(after=anchor.id)


# --- mark_read / mark_all_read ----------------------------------------------


async def test_mark_read_sets_read_at(repo: NotificationRepository, org: Organization) -> None:
    notification = await _send(repo, org.id)

    updated = await repo.mark_read(notification.id)

    assert updated.read_at is not None


async def test_mark_unread_clears_read_at(repo: NotificationRepository) -> None:
    notification = await _send(repo, None)
    await repo.mark_read(notification.id)

    updated = await repo.mark_unread(notification.id)

    assert updated.read_at is None


async def test_mark_all_read_only_affects_unread_for_that_user(
    repo: NotificationRepository, other_repo: NotificationRepository, org: Organization
) -> None:
    first = await _send(repo, org.id)
    second = await _send(repo, org.id)
    await repo.mark_read(first.id)
    theirs = await _send(other_repo, org.id)

    updated_count = await repo.mark_all_read()

    assert updated_count == 1
    assert (await repo.get_or_raise(second.id)).read_at is not None
    assert (await other_repo.get_or_raise(theirs.id)).read_at is None


# --- list_page ----------------------------------------------------------------


async def test_list_page_orders_newest_first_and_paginates(
    repo: NotificationRepository, org: Organization
) -> None:
    for _ in range(3):
        await _send(repo, org.id)

    page_one, cursor = await repo.list_page(limit=2)
    assert len(page_one) == 2
    assert cursor == page_one[-1].id

    page_two, cursor_two = await repo.list_page(after=cursor, limit=2)
    assert len(page_two) == 1
    assert cursor_two is None
    assert {n.id for n in page_one} & {n.id for n in page_two} == set()


async def test_list_page_unread_only_filters_read(
    repo: NotificationRepository, org: Organization
) -> None:
    read_notification = await _send(repo, org.id)
    unread_notification = await _send(repo, org.id)
    await repo.mark_read(read_notification.id)

    items, _ = await repo.list_page(unread_only=True)

    assert [n.id for n in items] == [unread_notification.id]


async def test_list_page_refuses_a_cursor_from_another_user(
    repo: NotificationRepository, other_repo: NotificationRepository, org: Organization
) -> None:
    """Repo finding 13. Another user's notification id is still
    unresolvable, so its position never leaks -- it just says so now.

    Treating it as "no cursor" returned this user's *first* page, which a
    feed asking to continue appends: the same rows twice, with a 200.
    """
    other_notification = await _send(other_repo, org.id)
    await _send(repo, org.id)

    with pytest.raises(InvalidCursorError):
        await repo.list_page(after=other_notification.id)


# --- find_recent_by_idempotency_key -------------------------------------------


async def test_find_recent_by_idempotency_key_matches_within_window(
    repo: NotificationRepository, org: Organization
) -> None:
    notification = await _send(repo, org.id, idempotency_key="evt_123")

    found = await repo.find_recent_by_idempotency_key(org.id, "evt_123")

    assert found is not None
    assert found.id == notification.id


async def test_find_recent_by_idempotency_key_matches_a_personal_one(
    repo: NotificationRepository,
) -> None:
    notification = await _send(repo, None, idempotency_key="evt_124")

    found = await repo.find_recent_by_idempotency_key(None, "evt_124")

    assert found is not None
    assert found.id == notification.id


async def test_find_recent_by_idempotency_key_ignores_stale_rows(
    repo: NotificationRepository, org: Organization, session: AsyncSession
) -> None:
    notification = await _send(repo, org.id, idempotency_key="evt_456")
    notification.created_at = datetime.now(UTC) - timedelta(hours=25)
    await session.flush()

    found = await repo.find_recent_by_idempotency_key(org.id, "evt_456")

    assert found is None


async def test_find_recent_by_idempotency_key_scoped_to_user_and_org(
    session: AsyncSession,
    repo: NotificationRepository,
    other_repo: NotificationRepository,
    user: User,
    org: Organization,
) -> None:
    await _send(other_repo, org.id, idempotency_key="evt_789")
    await _send(repo, None, idempotency_key="evt_789")
    second_org = await _org(session, "Globex", user)

    assert await repo.find_recent_by_idempotency_key(org.id, "evt_789") is None
    assert await repo.find_recent_by_idempotency_key(second_org.id, "evt_789") is None


# --- unread_count -------------------------------------------------------------


async def test_unread_count_counts_only_unread(
    repo: NotificationRepository, org: Organization
) -> None:
    await _send(repo, org.id)
    read = await _send(repo, org.id)
    await repo.mark_read(read.id)

    assert await repo.unread_count() == 1


async def test_unread_count_cache_hit_skips_the_database(
    repo: NotificationRepository, org: Organization, test_engine: AsyncEngine
) -> None:
    await _send(repo, org.id)
    await repo.unread_count()  # populates the cache

    query_count = 0

    def _count_queries(*_args: object, **_kwargs: object) -> None:
        nonlocal query_count
        query_count += 1

    event.listen(test_engine.sync_engine, "before_cursor_execute", _count_queries)
    try:
        cached_count = await repo.unread_count()
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", _count_queries)

    assert cached_count == 1
    assert query_count == 0


async def test_unread_count_invalidated_by_mark_read(
    repo: NotificationRepository, org: Organization
) -> None:
    notification = await _send(repo, org.id)
    await repo.unread_count()

    await repo.mark_read(notification.id)

    assert await repo.unread_count() == 0


# --- PushTokenRepository -------------------------------------------------------


async def test_register_creates_new_token(session: AsyncSession, user: User) -> None:
    repo_tokens = PushTokenRepository(session)

    token = await repo_tokens.register(user.id, "ExponentPushToken[a]", "ios", "iPhone")

    assert token.user_id == user.id
    assert token.platform == "ios"


async def test_register_upserts_by_token_and_reactivates(
    session: AsyncSession, user: User, other_user: User
) -> None:
    repo_tokens = PushTokenRepository(session)
    token = await repo_tokens.register(user.id, "ExponentPushToken[b]", "ios")
    await repo_tokens.deactivate(token.token)

    reregistered = await repo_tokens.register(
        other_user.id, "ExponentPushToken[b]", "android", "Pixel"
    )

    assert reregistered.id == token.id
    assert reregistered.user_id == other_user.id
    assert reregistered.platform == "android"
    assert reregistered.deleted_at is None


async def test_list_active_for_user_excludes_deactivated(session: AsyncSession, user: User) -> None:
    repo_tokens = PushTokenRepository(session)
    active = await repo_tokens.register(user.id, "ExponentPushToken[c]", "ios")
    inactive = await repo_tokens.register(user.id, "ExponentPushToken[d]", "ios")
    await repo_tokens.deactivate(inactive.token)

    tokens = await repo_tokens.list_active_for_user(user.id)

    assert [t.id for t in tokens] == [active.id]


async def test_deactivate_unknown_token_is_a_noop(session: AsyncSession) -> None:
    repo_tokens = PushTokenRepository(session)

    await repo_tokens.deactivate("ExponentPushToken[does-not-exist]")
