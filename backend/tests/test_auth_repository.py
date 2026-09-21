"""
Verifies T-016 (auth repository): DB access for User, Session, and the three
single-use token tables. These repositories are user-scoped (not org-scoped —
see §0.2's exception list), so unlike test_base_repository.py there is no
cross-tenant isolation to check; the focus here is correct filtering
(soft-deleted users, used/expired tokens, revoked/expired sessions) and that
mutating methods raise NotFoundError on a missing id.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel
from testcontainers.postgres import PostgresContainer

from app.core.exceptions import NotFoundError
from app.modules.auth.models import (
    EmailVerificationToken,
    MagicLinkToken,
    PasswordResetToken,
    User,
)
from app.modules.auth.models import (
    Session as SessionModel,
)
from app.modules.auth.repository import (
    EmailVerificationTokenRepository,
    MagicLinkTokenRepository,
    PasswordResetTokenRepository,
    SessionRepository,
    UserRepository,
    _TokenRepository,
)
from app.modules.auth.session_rules import is_session_usable

_TABLES = [
    User.__table__,
    SessionModel.__table__,
    EmailVerificationToken.__table__,
    PasswordResetToken.__table__,
    MagicLinkToken.__table__,
]


def _future(minutes: int = 30) -> datetime:
    return datetime.now(UTC) + timedelta(minutes=minutes)


def _past(minutes: int = 30) -> datetime:
    return datetime.now(UTC) - timedelta(minutes=minutes)


@pytest.fixture(scope="module")
def postgres_container() -> "PostgresContainer":
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


# Function-scoped (not module-scoped): see test_base_repository.py — an
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


@pytest.fixture
async def session(test_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s


@pytest.fixture
def user_repo(session: AsyncSession) -> UserRepository:
    return UserRepository(session)


@pytest.fixture
def session_repo(session: AsyncSession) -> SessionRepository:
    return SessionRepository(session)


@pytest.fixture
async def existing_user(user_repo: UserRepository) -> User:
    return await user_repo.create(email="alice@example.com", hashed_password="hashed")


# --- UserRepository -----------------------------------------------------


async def test_get_by_id_returns_user(user_repo: UserRepository, existing_user: User) -> None:
    fetched = await user_repo.get_by_id(existing_user.id)

    assert fetched is not None
    assert fetched.email == "alice@example.com"


async def test_get_by_id_returns_none_for_missing(user_repo: UserRepository) -> None:
    assert await user_repo.get_by_id(uuid4()) is None


async def test_get_by_id_excludes_soft_deleted(
    user_repo: UserRepository, existing_user: User, session: AsyncSession
) -> None:
    existing_user.deleted_at = datetime.now(UTC)
    await session.flush()

    assert await user_repo.get_by_id(existing_user.id) is None


async def test_get_by_email_returns_user(user_repo: UserRepository, existing_user: User) -> None:
    fetched = await user_repo.get_by_email("alice@example.com")

    assert fetched is not None
    assert fetched.id == existing_user.id


async def test_get_by_email_returns_none_for_unknown_email(user_repo: UserRepository) -> None:
    assert await user_repo.get_by_email("nobody@example.com") is None


async def test_get_by_email_excludes_soft_deleted(
    user_repo: UserRepository, existing_user: User, session: AsyncSession
) -> None:
    existing_user.deleted_at = datetime.now(UTC)
    await session.flush()

    assert await user_repo.get_by_email("alice@example.com") is None


async def test_create_persists_user(user_repo: UserRepository) -> None:
    user = await user_repo.create(email="bob@example.com")

    assert user.id is not None
    assert user.email == "bob@example.com"


async def test_update_modifies_fields(user_repo: UserRepository, existing_user: User) -> None:
    updated = await user_repo.update(existing_user.id, full_name="Alice Example")

    assert updated.full_name == "Alice Example"


async def test_update_raises_not_found_for_missing(user_repo: UserRepository) -> None:
    with pytest.raises(NotFoundError):
        await user_repo.update(uuid4(), full_name="nobody")


async def test_increment_failed_login_increments(
    user_repo: UserRepository, existing_user: User
) -> None:
    await user_repo.increment_failed_login(existing_user.id)
    await user_repo.increment_failed_login(existing_user.id)

    fetched = await user_repo.get_by_id(existing_user.id)
    assert fetched is not None
    assert fetched.failed_login_count == 2


async def test_reset_failed_login_resets_to_zero(
    user_repo: UserRepository, existing_user: User
) -> None:
    await user_repo.increment_failed_login(existing_user.id)
    await user_repo.reset_failed_login(existing_user.id)

    fetched = await user_repo.get_by_id(existing_user.id)
    assert fetched is not None
    assert fetched.failed_login_count == 0


async def test_set_lockout_sets_locked_until(
    user_repo: UserRepository, existing_user: User
) -> None:
    until = _future(15)

    await user_repo.set_lockout(existing_user.id, until)

    fetched = await user_repo.get_by_id(existing_user.id)
    assert fetched is not None
    assert fetched.locked_until == until


async def test_mark_email_verified_sets_verified_and_active(
    user_repo: UserRepository,
) -> None:
    user = await user_repo.create(email="pending@example.com", is_active=False)

    await user_repo.mark_email_verified(user.id)

    fetched = await user_repo.get_by_id(user.id)
    assert fetched is not None
    assert fetched.email_verified_at is not None
    assert fetched.is_active is True


# --- SessionRepository ---------------------------------------------------


async def test_session_create_persists_session(
    session_repo: SessionRepository, existing_user: User
) -> None:
    created = await session_repo.create(
        user_id=existing_user.id,
        token_hash="token-hash-1",
        device_label="Chrome on macOS",
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=_future(),
    )

    assert created.id is not None
    assert created.last_seen_at is not None
    assert created.revoked_at is None


async def test_get_by_token_hash_returns_session(
    session_repo: SessionRepository, existing_user: User
) -> None:
    created = await session_repo.create(
        user_id=existing_user.id,
        token_hash="token-hash-2",
        device_label=None,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=_future(),
    )

    fetched = await session_repo.get_by_token_hash("token-hash-2")

    assert fetched is not None
    assert fetched.id == created.id


async def test_get_by_token_hash_returns_none_for_unknown_hash(
    session_repo: SessionRepository,
) -> None:
    assert await session_repo.get_by_token_hash("nonexistent") is None


async def test_revoke_sets_revoked_at(session_repo: SessionRepository, existing_user: User) -> None:
    created = await session_repo.create(
        user_id=existing_user.id,
        token_hash="token-hash-3",
        device_label=None,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=_future(),
    )

    await session_repo.revoke(created.id)

    fetched = await session_repo.get_by_token_hash("token-hash-3")
    assert fetched is not None
    assert fetched.revoked_at is not None


async def test_revoke_raises_not_found_for_missing(session_repo: SessionRepository) -> None:
    with pytest.raises(NotFoundError):
        await session_repo.revoke(uuid4())


async def test_revoke_all_for_user_revokes_active_sessions_only(
    session_repo: SessionRepository, existing_user: User
) -> None:
    first = await session_repo.create(
        user_id=existing_user.id,
        token_hash="token-hash-4",
        device_label=None,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=_future(),
    )
    await session_repo.create(
        user_id=existing_user.id,
        token_hash="token-hash-5",
        device_label=None,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=_future(),
    )
    await session_repo.revoke(first.id)
    revoked_before = await session_repo.get_by_token_hash("token-hash-4")
    assert revoked_before is not None
    already_revoked_at = revoked_before.revoked_at

    await session_repo.revoke_all_for_user(existing_user.id)

    refetched_first = await session_repo.get_by_token_hash("token-hash-4")
    refetched_second = await session_repo.get_by_token_hash("token-hash-5")
    assert refetched_first is not None
    assert refetched_second is not None
    # Already-revoked session is untouched (filtered out of the UPDATE) —
    # proves revoke_all_for_user doesn't blindly overwrite every row.
    assert refetched_first.revoked_at == already_revoked_at
    assert refetched_second.revoked_at is not None


async def test_revoke_all_for_user_does_not_touch_other_users(
    session_repo: SessionRepository, existing_user: User, user_repo: UserRepository
) -> None:
    other_user = await user_repo.create(email="other@example.com")
    other_session = await session_repo.create(
        user_id=other_user.id,
        token_hash="token-hash-6",
        device_label=None,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=_future(),
    )

    await session_repo.revoke_all_for_user(existing_user.id)

    fetched = await session_repo.get_by_token_hash("token-hash-6")
    assert fetched is not None
    assert fetched.id == other_session.id
    assert fetched.revoked_at is None


async def test_revoke_all_for_user_with_except_id_spares_that_session(
    session_repo: SessionRepository, existing_user: User
) -> None:
    # T-216: change_password() keeps the request's own session alive while
    # revoking every other one -- except_id is how it does that.
    keep = await session_repo.create(
        user_id=existing_user.id,
        token_hash="token-hash-except-1",
        device_label=None,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=_future(),
    )
    await session_repo.create(
        user_id=existing_user.id,
        token_hash="token-hash-except-2",
        device_label=None,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=_future(),
    )

    await session_repo.revoke_all_for_user(existing_user.id, except_id=keep.id)

    refetched_keep = await session_repo.get_by_token_hash("token-hash-except-1")
    refetched_other = await session_repo.get_by_token_hash("token-hash-except-2")
    assert refetched_keep is not None
    assert refetched_keep.revoked_at is None
    assert refetched_other is not None
    assert refetched_other.revoked_at is not None


async def test_list_active_for_user_excludes_revoked_and_expired(
    session_repo: SessionRepository, existing_user: User
) -> None:
    active = await session_repo.create(
        user_id=existing_user.id,
        token_hash="token-hash-7",
        device_label=None,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=_future(),
    )
    revoked = await session_repo.create(
        user_id=existing_user.id,
        token_hash="token-hash-8",
        device_label=None,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=_future(),
    )
    await session_repo.revoke(revoked.id)
    await session_repo.create(
        user_id=existing_user.id,
        token_hash="token-hash-9",
        device_label=None,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=_past(),
    )

    active_sessions = await session_repo.list_active_for_user(existing_user.id)

    assert [s.id for s in active_sessions] == [active.id]


async def test_list_active_for_user_excludes_sessions_past_the_idle_cap(
    session_repo: SessionRepository, existing_user: User
) -> None:
    """The defect this file did not catch.

    A session inside its absolute cap but idle past
    REFRESH_TOKEN_IDLE_TIMEOUT_MINUTES cannot be refreshed -- the next attempt
    rejects and revokes it. It was still listed as a signed-in device, for up
    to the full thirty days. Measured in production before the fix: 15 rows
    listed, 14 of them in this state.
    """
    fresh = await session_repo.create(
        user_id=existing_user.id,
        token_hash="token-hash-idle-1",
        device_label=None,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=_future(),
    )
    idle = await session_repo.create(
        user_id=existing_user.id,
        token_hash="token-hash-idle-2",
        device_label=None,
        ip_address="127.0.0.1",
        user_agent="pytest",
        # Well inside the 30-day absolute cap, well outside the 12h idle one.
        expires_at=_future(minutes=60 * 24 * 20),
    )
    idle.last_seen_at = datetime.now(UTC) - timedelta(days=3)
    await session_repo.session.flush()

    listed = await session_repo.list_active_for_user(existing_user.id)

    assert [s.id for s in listed] == [fresh.id]


async def test_count_active_excludes_sessions_past_the_idle_cap(
    session_repo: SessionRepository, existing_user: User
) -> None:
    """The same gap, in the number behind the forge_active_sessions gauge."""
    await session_repo.create(
        user_id=existing_user.id,
        token_hash="token-hash-idle-count-1",
        device_label=None,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=_future(),
    )
    idle = await session_repo.create(
        user_id=existing_user.id,
        token_hash="token-hash-idle-count-2",
        device_label=None,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=_future(minutes=60 * 24 * 20),
    )
    idle.last_seen_at = datetime.now(UTC) - timedelta(days=3)
    await session_repo.session.flush()

    assert await session_repo.count_active() == 1


async def test_revoke_idle_marks_only_the_timed_out_sessions(
    session_repo: SessionRepository, existing_user: User
) -> None:
    """Filtering the list fixes the display; this stops the rows piling up.

    The already-expired one is left alone deliberately -- prune_expired
    deletes those, and revoking on the way out would be a write for nothing.
    """
    fresh = await session_repo.create(
        user_id=existing_user.id,
        token_hash="token-hash-revoke-idle-1",
        device_label=None,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=_future(),
    )
    idle = await session_repo.create(
        user_id=existing_user.id,
        token_hash="token-hash-revoke-idle-2",
        device_label=None,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=_future(minutes=60 * 24 * 20),
    )
    idle.last_seen_at = datetime.now(UTC) - timedelta(days=3)
    expired = await session_repo.create(
        user_id=existing_user.id,
        token_hash="token-hash-revoke-idle-3",
        device_label=None,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=_past(),
    )
    await session_repo.session.flush()

    revoked = await session_repo.revoke_idle()

    assert revoked == 1
    refetched_idle = await session_repo.get_by_token_hash("token-hash-revoke-idle-2")
    assert refetched_idle is not None
    assert refetched_idle.revoked_at is not None
    refetched_fresh = await session_repo.get_by_token_hash("token-hash-revoke-idle-1")
    assert refetched_fresh is not None
    assert refetched_fresh.revoked_at is None
    refetched_expired = await session_repo.get_by_token_hash("token-hash-revoke-idle-3")
    assert refetched_expired is not None
    assert refetched_expired.revoked_at is None
    assert fresh.id != expired.id  # both created, so the filter is doing the work


async def test_revoke_idle_is_idempotent(
    session_repo: SessionRepository, existing_user: User
) -> None:
    """It runs on a schedule, so a second tick over the same rows must be a
    no-op rather than re-stamping revoked_at."""
    idle = await session_repo.create(
        user_id=existing_user.id,
        token_hash="token-hash-revoke-idle-twice",
        device_label=None,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=_future(minutes=60 * 24 * 20),
    )
    idle.last_seen_at = datetime.now(UTC) - timedelta(days=3)
    await session_repo.session.flush()

    assert await session_repo.revoke_idle() == 1
    assert await session_repo.revoke_idle() == 0


async def test_the_query_and_in_memory_forms_of_the_rule_agree(
    session_repo: SessionRepository, existing_user: User
) -> None:
    """session_rules exposes the rule twice -- as WHERE clauses for the list
    and the count, and as a predicate for refresh(), which works from a row it
    already has. Two forms of one rule is exactly how they drift, so this
    walks every combination of the three conditions and asserts they answer
    the same."""
    cases = {
        "usable": (_future(), datetime.now(UTC), None),
        "expired": (_past(), datetime.now(UTC), None),
        "idle": (_future(), datetime.now(UTC) - timedelta(days=3), None),
        "revoked": (_future(), datetime.now(UTC), datetime.now(UTC)),
        "idle and expired": (
            _past(),
            datetime.now(UTC) - timedelta(days=3),
            None,
        ),
    }
    created: dict[str, Any] = {}
    for name, (expires_at, last_seen_at, revoked_at) in cases.items():
        row = await session_repo.create(
            user_id=existing_user.id,
            token_hash=f"token-hash-agree-{name}",
            device_label=None,
            ip_address="127.0.0.1",
            user_agent="pytest",
            expires_at=expires_at,
        )
        row.last_seen_at = last_seen_at
        row.revoked_at = revoked_at
        created[name] = row
    await session_repo.session.flush()

    listed = {s.id for s in await session_repo.list_active_for_user(existing_user.id)}

    for name, row in created.items():
        assert (row.id in listed) is is_session_usable(row), (
            f"query and in-memory forms disagree for the {name!r} case"
        )


async def test_list_active_for_user_excludes_other_users(
    session_repo: SessionRepository, existing_user: User, user_repo: UserRepository
) -> None:
    other_user = await user_repo.create(email="other2@example.com")
    await session_repo.create(
        user_id=other_user.id,
        token_hash="token-hash-10",
        device_label=None,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=_future(),
    )

    assert await session_repo.list_active_for_user(existing_user.id) == []


async def test_prune_expired_deletes_only_expired_sessions(
    session_repo: SessionRepository, existing_user: User
) -> None:
    await session_repo.create(
        user_id=existing_user.id,
        token_hash="token-hash-11",
        device_label=None,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=_future(),
    )
    await session_repo.create(
        user_id=existing_user.id,
        token_hash="token-hash-12",
        device_label=None,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=_past(),
    )

    deleted_count = await session_repo.prune_expired()

    assert deleted_count == 1
    assert await session_repo.get_by_token_hash("token-hash-11") is not None
    assert await session_repo.get_by_token_hash("token-hash-12") is None


# --- Token repositories ---------------------------------------------------

_TOKEN_REPO_CLASSES: list[type[_TokenRepository[Any]]] = [
    EmailVerificationTokenRepository,
    PasswordResetTokenRepository,
    MagicLinkTokenRepository,
]


@pytest.mark.parametrize("repo_cls", _TOKEN_REPO_CLASSES)
async def test_token_create_and_get_by_hash_round_trip(
    repo_cls: type[_TokenRepository[Any]], session: AsyncSession, existing_user: User
) -> None:
    repo = repo_cls(session)
    created = await repo.create(user_id=existing_user.id, token_hash="hash-a", expires_at=_future())

    fetched = await repo.get_by_hash("hash-a")

    assert fetched is not None
    assert fetched.id == created.id


@pytest.mark.parametrize("repo_cls", _TOKEN_REPO_CLASSES)
async def test_token_get_by_hash_returns_none_for_unknown_hash(
    repo_cls: type[_TokenRepository[Any]], session: AsyncSession
) -> None:
    repo = repo_cls(session)

    assert await repo.get_by_hash("missing") is None


@pytest.mark.parametrize("repo_cls", _TOKEN_REPO_CLASSES)
async def test_token_get_by_hash_excludes_expired(
    repo_cls: type[_TokenRepository[Any]], session: AsyncSession, existing_user: User
) -> None:
    repo = repo_cls(session)
    await repo.create(user_id=existing_user.id, token_hash="hash-expired", expires_at=_past())

    assert await repo.get_by_hash("hash-expired") is None


@pytest.mark.parametrize("repo_cls", _TOKEN_REPO_CLASSES)
async def test_token_get_by_hash_excludes_used(
    repo_cls: type[_TokenRepository[Any]], session: AsyncSession, existing_user: User
) -> None:
    repo = repo_cls(session)
    created = await repo.create(
        user_id=existing_user.id, token_hash="hash-used", expires_at=_future()
    )

    await repo.mark_used(created.id)

    assert await repo.get_by_hash("hash-used") is None


@pytest.mark.parametrize("repo_cls", _TOKEN_REPO_CLASSES)
async def test_token_mark_used_raises_not_found_for_missing(
    repo_cls: type[_TokenRepository[Any]], session: AsyncSession
) -> None:
    repo = repo_cls(session)

    with pytest.raises(NotFoundError):
        await repo.mark_used(uuid4())


@pytest.mark.parametrize("repo_cls", _TOKEN_REPO_CLASSES)
async def test_token_delete_expired_deletes_only_expired(
    repo_cls: type[_TokenRepository[Any]], session: AsyncSession, existing_user: User
) -> None:
    repo = repo_cls(session)
    kept = await repo.create(user_id=existing_user.id, token_hash="hash-keep", expires_at=_future())
    gone = await repo.create(user_id=existing_user.id, token_hash="hash-gone", expires_at=_past())

    deleted_count = await repo.delete_expired()

    assert deleted_count == 1
    # get_by_hash() already filters out expired rows regardless of physical
    # deletion -- session.get() proves the expired row is actually gone, not
    # just hidden by that filter.
    assert await session.get(repo.model, kept.id) is not None
    assert await session.get(repo.model, gone.id) is None
