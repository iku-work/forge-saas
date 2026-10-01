"""
Verifies T-019 (auth service — token refresh + theft detection): happy-path
rotation revokes the old session and issues a new one, while replaying a
revoked or expired refresh token revokes every session the user has. Uses
the same real-Postgres testcontainers pattern as test_auth_service_login.py.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from app.core.config import get_settings
from app.core.exceptions import AuthenticationError
from app.modules.auth.models import User
from app.modules.auth.repository import SessionRepository, UserRepository
from app.modules.auth.service import AuthService
from app.modules.auth.utils import (
    generate_refresh_token,
    hash_password,
    hash_token,
    verify_access_token,
)

_VALID_PASSWORD = "Correct-Horse9"
_IP = "127.0.0.1"
_USER_AGENT = "pytest"


@pytest.fixture
def service(session: AsyncSession) -> AuthService:
    return AuthService(session)


@pytest.fixture
def user_repo(session: AsyncSession) -> UserRepository:
    return UserRepository(session)


@pytest.fixture
def session_repo(session: AsyncSession) -> SessionRepository:
    return SessionRepository(session)


@pytest.fixture
async def verified_user(user_repo: UserRepository) -> User:
    user = await user_repo.create(
        email="alice@example.com",
        hashed_password=hash_password(_VALID_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    # Committed throughout this file, as separate requests would have: a
    # rejected refresh rolls back before it persists its consequence
    # (persist_then_reject), and anything left uncommitted would go with it.
    await user_repo.session.commit()
    return user


async def _issue_session(
    session_repo: SessionRepository,
    user: User,
    *,
    expires_at: datetime | None = None,
) -> str:
    """Creates a Session row directly and hands back the plain refresh token."""
    plain, token_hash = generate_refresh_token()
    await session_repo.create(
        user_id=user.id,
        token_hash=token_hash,
        device_label=None,
        ip_address=_IP,
        user_agent=_USER_AGENT,
        expires_at=expires_at or datetime.now(UTC) + timedelta(days=30),
    )
    await session_repo.session.commit()
    return plain


# --- happy path ----------------------------------------------------------------


async def test_refresh_happy_path_returns_new_tokens(
    service: AuthService, verified_user: User, session_repo: SessionRepository
) -> None:
    plain = await _issue_session(session_repo, verified_user)

    access_token, new_refresh_plain = await service.refresh(plain, _IP, _USER_AGENT)

    assert access_token
    assert new_refresh_plain
    assert new_refresh_plain != plain


async def test_refresh_happy_path_revokes_old_session(
    service: AuthService, verified_user: User, session_repo: SessionRepository
) -> None:
    plain = await _issue_session(session_repo, verified_user)

    await service.refresh(plain, _IP, _USER_AGENT)

    old_session = await session_repo.get_by_token_hash(hash_token(plain))
    assert old_session is not None
    assert old_session.revoked_at is not None


async def test_refresh_happy_path_creates_exactly_one_new_active_session(
    service: AuthService, verified_user: User, session_repo: SessionRepository
) -> None:
    plain = await _issue_session(session_repo, verified_user)

    await service.refresh(plain, _IP, _USER_AGENT)

    active = await session_repo.list_active_for_user(verified_user.id)
    assert len(active) == 1


async def test_refresh_new_token_can_be_used_again(
    service: AuthService, verified_user: User, session_repo: SessionRepository
) -> None:
    plain = await _issue_session(session_repo, verified_user)

    _, second_plain = await service.refresh(plain, _IP, _USER_AGENT)
    access_token, third_plain = await service.refresh(second_plain, _IP, _USER_AGENT)

    assert access_token
    assert third_plain


# --- theft detection ---------------------------------------------------------------


async def test_refresh_unknown_token_raises(service: AuthService) -> None:
    with pytest.raises(AuthenticationError):
        await service.refresh("not-a-real-token", _IP, _USER_AGENT)


@pytest.mark.owasp("A07")
async def test_refresh_reuse_of_revoked_token_raises(
    service: AuthService, verified_user: User, session_repo: SessionRepository
) -> None:
    plain = await _issue_session(session_repo, verified_user)
    await service.refresh(plain, _IP, _USER_AGENT)  # rotates -- revokes `plain`'s session
    await session_repo.session.commit()

    with pytest.raises(AuthenticationError):
        await service.refresh(plain, _IP, _USER_AGENT)


@pytest.mark.owasp("A07")
async def test_refresh_reuse_of_revoked_token_revokes_all_user_sessions(
    service: AuthService, verified_user: User, session_repo: SessionRepository
) -> None:
    """Service-level: what refresh() does, not what reaches the database.

    This cannot show that the revocation survives the 401 -- it once passed
    while production rolled the revocation back (review R2). That property is
    test_auth_router.py's test_replaying_a_rotated_refresh_token_signs_out_every_device,
    which goes through get_session and reads back through a fresh session.
    """
    user_id = verified_user.id
    plain = await _issue_session(session_repo, verified_user)
    _, rotated_plain = await service.refresh(plain, _IP, _USER_AGENT)
    await session_repo.session.commit()

    with pytest.raises(AuthenticationError):
        await service.refresh(plain, _IP, _USER_AGENT)  # replay of the now-revoked token

    active = await session_repo.list_active_for_user(user_id)
    assert active == []
    # Even the session created by the legitimate rotation above is gone.
    rotated_session = await session_repo.get_by_token_hash(hash_token(rotated_plain))
    assert rotated_session is not None
    assert rotated_session.revoked_at is not None


async def test_refresh_expired_token_raises(
    service: AuthService, verified_user: User, session_repo: SessionRepository
) -> None:
    plain = await _issue_session(
        session_repo, verified_user, expires_at=datetime.now(UTC) - timedelta(minutes=1)
    )

    with pytest.raises(AuthenticationError):
        await service.refresh(plain, _IP, _USER_AGENT)


# --- T-218: periodic re-authentication (absolute cap + idle timeout) -------------


async def test_refresh_expired_token_does_not_revoke_other_sessions(
    service: AuthService, verified_user: User, session_repo: SessionRepository
) -> None:
    """Unlike replaying a *revoked* token (theft signal, revokes everything),
    a session that simply outlived its absolute cap is ordinary aging -- only
    that one chain is rejected."""
    plain = await _issue_session(
        session_repo, verified_user, expires_at=datetime.now(UTC) - timedelta(minutes=1)
    )
    other_plain = await _issue_session(session_repo, verified_user)

    with pytest.raises(AuthenticationError):
        await service.refresh(plain, _IP, _USER_AGENT)

    other_session = await session_repo.get_by_token_hash(hash_token(other_plain))
    assert other_session is not None
    assert other_session.revoked_at is None


async def test_refresh_propagates_auth_time_unchanged_across_rotation(
    service: AuthService, verified_user: User, session_repo: SessionRepository
) -> None:
    plain = await _issue_session(session_repo, verified_user)
    original = await session_repo.get_by_token_hash(hash_token(plain))
    assert original is not None

    access_token, new_plain = await service.refresh(plain, _IP, _USER_AGENT)

    new_session = await session_repo.get_by_token_hash(hash_token(new_plain))
    assert new_session is not None
    assert new_session.auth_time == original.auth_time
    assert verify_access_token(access_token).auth_time == original.auth_time


async def test_refresh_keeps_absolute_expiry_anchored_not_sliding(
    service: AuthService, verified_user: User, session_repo: SessionRepository
) -> None:
    plain = await _issue_session(session_repo, verified_user)
    original = await session_repo.get_by_token_hash(hash_token(plain))
    assert original is not None

    _, new_plain = await service.refresh(plain, _IP, _USER_AGENT)

    new_session = await session_repo.get_by_token_hash(hash_token(new_plain))
    assert new_session is not None
    assert new_session.expires_at == original.expires_at


async def test_refresh_idle_timeout_rejected(
    service: AuthService,
    verified_user: User,
    session_repo: SessionRepository,
    session: AsyncSession,
) -> None:
    plain = await _issue_session(session_repo, verified_user)
    stale = await session_repo.get_by_token_hash(hash_token(plain))
    assert stale is not None
    settings = get_settings()
    stale.last_seen_at = datetime.now(UTC) - timedelta(
        minutes=settings.REFRESH_TOKEN_IDLE_TIMEOUT_MINUTES + 1
    )
    await session.commit()

    with pytest.raises(AuthenticationError):
        await service.refresh(plain, _IP, _USER_AGENT)


async def test_refresh_idle_timeout_does_not_revoke_other_sessions(
    service: AuthService,
    verified_user: User,
    session_repo: SessionRepository,
    session: AsyncSession,
) -> None:
    plain = await _issue_session(session_repo, verified_user)
    stale = await session_repo.get_by_token_hash(hash_token(plain))
    assert stale is not None
    settings = get_settings()
    stale.last_seen_at = datetime.now(UTC) - timedelta(
        minutes=settings.REFRESH_TOKEN_IDLE_TIMEOUT_MINUTES + 1
    )
    await session.commit()
    other_plain = await _issue_session(session_repo, verified_user)

    with pytest.raises(AuthenticationError):
        await service.refresh(plain, _IP, _USER_AGENT)

    other_session = await session_repo.get_by_token_hash(hash_token(other_plain))
    assert other_session is not None
    assert other_session.revoked_at is None


async def test_refresh_within_idle_window_succeeds(
    service: AuthService,
    verified_user: User,
    session_repo: SessionRepository,
    session: AsyncSession,
) -> None:
    plain = await _issue_session(session_repo, verified_user)
    fresh = await session_repo.get_by_token_hash(hash_token(plain))
    assert fresh is not None
    settings = get_settings()
    fresh.last_seen_at = datetime.now(UTC) - timedelta(
        minutes=settings.REFRESH_TOKEN_IDLE_TIMEOUT_MINUTES - 1
    )
    await session.flush()

    access_token, new_plain = await service.refresh(plain, _IP, _USER_AGENT)

    assert access_token
    assert new_plain
