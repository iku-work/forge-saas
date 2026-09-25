"""
Verifies T-020 (auth service — password reset + magic link): both request
flows are enumeration-safe (silent `None` for both known and unknown
emails, with a token created only for the known case), both tokens are
single-use and time-bound, and a successful password reset revokes every
existing session. Uses the same real-Postgres testcontainers pattern as
test_auth_service_refresh.py.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel
from testcontainers.postgres import PostgresContainer

from app.core.exceptions import AuthenticationError
from app.modules.auth.models import MagicLinkToken, PasswordResetToken, User, WebAuthnCredential
from app.modules.auth.models import Session as SessionModel
from app.modules.auth.repository import (
    MagicLinkTokenRepository,
    PasswordResetTokenRepository,
    SessionRepository,
    UserRepository,
)
from app.modules.auth.service import AuthService, TwoFactorChallenge
from app.modules.auth.utils import (
    generate_refresh_token,
    hash_password,
    hash_token,
    verify_password,
)

_TABLES = [
    User.__table__,
    SessionModel.__table__,
    PasswordResetToken.__table__,
    MagicLinkToken.__table__,
    # Every sign-in now asks whether the account has a second factor, and a
    # security key is one of the two answers -- see
    # AuthService._second_factor_challenge. Consuming a magic link reaches
    # that question like every other entry point does.
    WebAuthnCredential.__table__,
]
_VALID_PASSWORD = "Correct-Horse9"
_NEW_PASSWORD = "Even-Better-Horse8"
_IP = "127.0.0.1"
_USER_AGENT = "pytest"


@pytest.fixture(scope="module")
def postgres_container() -> "PostgresContainer":
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


# Function-scoped (not module-scoped): see test_auth_repository.py — an
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
def service(session: AsyncSession) -> AuthService:
    return AuthService(session)


@pytest.fixture
def user_repo(session: AsyncSession) -> UserRepository:
    return UserRepository(session)


@pytest.fixture
def session_repo(session: AsyncSession) -> SessionRepository:
    return SessionRepository(session)


@pytest.fixture
def password_reset_repo(session: AsyncSession) -> PasswordResetTokenRepository:
    return PasswordResetTokenRepository(session)


@pytest.fixture
def magic_link_repo(session: AsyncSession) -> MagicLinkTokenRepository:
    return MagicLinkTokenRepository(session)


@pytest.fixture
async def verified_user(user_repo: UserRepository) -> User:
    return await user_repo.create(
        email="alice@example.com",
        hashed_password=hash_password(_VALID_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )


async def _issue_session(session_repo: SessionRepository, user: User) -> str:
    plain, token_hash = generate_refresh_token()
    await session_repo.create(
        user_id=user.id,
        token_hash=token_hash,
        device_label=None,
        ip_address=_IP,
        user_agent=_USER_AGENT,
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    return plain


async def _issue_reset_token(
    repo: PasswordResetTokenRepository,
    user: User,
    *,
    plain: str = "plaintext-reset-token",
    expires_at: datetime | None = None,
) -> str:
    """Mirrors what AuthService.request_password_reset stores, but hands
    back the plain token directly (see test_auth_service.py's _issue_token)."""
    await repo.create(
        user_id=user.id,
        token_hash=hash_token(plain),
        expires_at=expires_at or datetime.now(UTC) + timedelta(hours=1),
    )
    return plain


async def _issue_magic_link_token(
    repo: MagicLinkTokenRepository,
    user: User,
    *,
    plain: str = "plaintext-magic-link-token",
    expires_at: datetime | None = None,
) -> str:
    await repo.create(
        user_id=user.id,
        token_hash=hash_token(plain),
        expires_at=expires_at or datetime.now(UTC) + timedelta(minutes=15),
    )
    return plain


# --- request_password_reset ---------------------------------------------------


async def test_request_password_reset_unknown_email_returns_none(service: AuthService) -> None:
    result = await service.request_password_reset("ghost@example.com")

    assert result is None


async def test_request_password_reset_known_email_returns_none(
    service: AuthService, verified_user: User
) -> None:
    result = await service.request_password_reset(verified_user.email)

    assert result is None


async def test_request_password_reset_unknown_email_creates_no_token(
    service: AuthService, session: AsyncSession
) -> None:
    await service.request_password_reset("ghost@example.com")

    count = (
        await session.execute(select(func.count()).select_from(PasswordResetToken))
    ).scalar_one()
    assert count == 0


async def test_request_password_reset_known_email_creates_token(
    service: AuthService, verified_user: User, session: AsyncSession
) -> None:
    await service.request_password_reset(verified_user.email)

    count = (
        await session.execute(
            select(func.count())
            .select_from(PasswordResetToken)
            .where(PasswordResetToken.user_id == verified_user.id)
        )
    ).scalar_one()
    assert count == 1


# --- confirm_password_reset ----------------------------------------------------


async def test_confirm_password_reset_updates_password(
    service: AuthService,
    verified_user: User,
    password_reset_repo: PasswordResetTokenRepository,
    user_repo: UserRepository,
) -> None:
    plain = await _issue_reset_token(password_reset_repo, verified_user)

    await service.confirm_password_reset(plain, _NEW_PASSWORD)

    updated = await user_repo.get_by_id(verified_user.id)
    assert updated is not None
    assert updated.hashed_password is not None
    assert verify_password(_NEW_PASSWORD, updated.hashed_password)
    assert not verify_password(_VALID_PASSWORD, updated.hashed_password)


async def test_confirm_password_reset_revokes_all_sessions(
    service: AuthService,
    verified_user: User,
    password_reset_repo: PasswordResetTokenRepository,
    session_repo: SessionRepository,
) -> None:
    await _issue_session(session_repo, verified_user)
    await _issue_session(session_repo, verified_user)
    plain = await _issue_reset_token(password_reset_repo, verified_user)

    await service.confirm_password_reset(plain, _NEW_PASSWORD)

    active = await session_repo.list_active_for_user(verified_user.id)
    assert active == []


async def test_confirm_password_reset_unknown_token_raises(service: AuthService) -> None:
    with pytest.raises(AuthenticationError):
        await service.confirm_password_reset("not-a-real-token", _NEW_PASSWORD)


async def test_confirm_password_reset_used_twice_raises_on_second_call(
    service: AuthService, verified_user: User, password_reset_repo: PasswordResetTokenRepository
) -> None:
    plain = await _issue_reset_token(password_reset_repo, verified_user)
    await service.confirm_password_reset(plain, _NEW_PASSWORD)

    with pytest.raises(AuthenticationError):
        await service.confirm_password_reset(plain, "Another-New-Pass1")


async def test_confirm_password_reset_expired_token_raises(
    service: AuthService, verified_user: User, password_reset_repo: PasswordResetTokenRepository
) -> None:
    plain = await _issue_reset_token(
        password_reset_repo,
        verified_user,
        plain="expired-reset-token",
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )

    with pytest.raises(AuthenticationError):
        await service.confirm_password_reset(plain, _NEW_PASSWORD)


async def test_confirm_password_reset_for_soft_deleted_user_raises(
    service: AuthService,
    verified_user: User,
    password_reset_repo: PasswordResetTokenRepository,
    session: AsyncSession,
) -> None:
    plain = await _issue_reset_token(password_reset_repo, verified_user)
    verified_user.deleted_at = datetime.now(UTC)
    await session.flush()

    with pytest.raises(AuthenticationError):
        await service.confirm_password_reset(plain, _NEW_PASSWORD)


# --- request_magic_link / consume_magic_link ------------------------------------


async def test_request_magic_link_unknown_email_returns_none(service: AuthService) -> None:
    result = await service.request_magic_link("ghost@example.com")

    assert result is None


async def test_request_magic_link_unknown_email_creates_no_token(
    service: AuthService, session: AsyncSession
) -> None:
    await service.request_magic_link("ghost@example.com")

    count = (await session.execute(select(func.count()).select_from(MagicLinkToken))).scalar_one()
    assert count == 0


async def test_request_magic_link_known_email_creates_token(
    service: AuthService, verified_user: User, session: AsyncSession
) -> None:
    await service.request_magic_link(verified_user.email)

    count = (
        await session.execute(
            select(func.count())
            .select_from(MagicLinkToken)
            .where(MagicLinkToken.user_id == verified_user.id)
        )
    ).scalar_one()
    assert count == 1


async def test_consume_magic_link_returns_session_tokens(
    service: AuthService, verified_user: User, magic_link_repo: MagicLinkTokenRepository
) -> None:
    plain = await _issue_magic_link_token(magic_link_repo, verified_user)

    access_token, refresh_plain, user = await service.consume_magic_link(plain, _IP, _USER_AGENT)

    assert access_token
    assert refresh_plain
    assert user.id == verified_user.id


async def test_consume_magic_link_creates_active_session(
    service: AuthService,
    verified_user: User,
    magic_link_repo: MagicLinkTokenRepository,
    session_repo: SessionRepository,
) -> None:
    plain = await _issue_magic_link_token(magic_link_repo, verified_user)

    await service.consume_magic_link(plain, _IP, _USER_AGENT)

    active = await session_repo.list_active_for_user(verified_user.id)
    assert len(active) == 1


async def test_consume_magic_link_unknown_token_raises(service: AuthService) -> None:
    with pytest.raises(AuthenticationError):
        await service.consume_magic_link("not-a-real-token", _IP, _USER_AGENT)


async def test_consume_magic_link_used_twice_raises_on_second_call(
    service: AuthService, verified_user: User, magic_link_repo: MagicLinkTokenRepository
) -> None:
    plain = await _issue_magic_link_token(magic_link_repo, verified_user)
    await service.consume_magic_link(plain, _IP, _USER_AGENT)

    with pytest.raises(AuthenticationError):
        await service.consume_magic_link(plain, _IP, _USER_AGENT)


async def test_consume_magic_link_expired_token_raises(
    service: AuthService, verified_user: User, magic_link_repo: MagicLinkTokenRepository
) -> None:
    plain = await _issue_magic_link_token(
        magic_link_repo,
        verified_user,
        plain="expired-magic-link-token",
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )

    with pytest.raises(AuthenticationError):
        await service.consume_magic_link(plain, _IP, _USER_AGENT)


async def test_consume_magic_link_for_soft_deleted_user_raises(
    service: AuthService,
    verified_user: User,
    magic_link_repo: MagicLinkTokenRepository,
    session: AsyncSession,
) -> None:
    plain = await _issue_magic_link_token(magic_link_repo, verified_user)
    verified_user.deleted_at = datetime.now(UTC)
    await session.flush()

    with pytest.raises(AuthenticationError):
        await service.consume_magic_link(plain, _IP, _USER_AGENT)


# --- locale-prefixed email deep links (T-233) ---------------------------------


async def test_password_reset_link_carries_the_recipients_locale(
    service: AuthService,
    verified_user: User,
    session: AsyncSession,
    enqueued_jobs: list[tuple[str, Any]],
) -> None:
    verified_user.locale = "fi"
    session.add(verified_user)
    await session.flush()

    await service.request_password_reset(verified_user.email)

    kwargs = next(k for f, k in enqueued_jobs if f == "send_email")
    assert kwargs["locale"] == "fi"
    assert "/fi/reset-password?token=" in kwargs["context"]["reset_url"]


async def test_magic_link_points_at_the_route_the_spa_actually_serves(
    service: AuthService,
    verified_user: User,
    session: AsyncSession,
    enqueued_jobs: list[tuple[str, Any]],
) -> None:
    """Pre-T-233 this link was "/magic-link/consume?token=" -- a path no
    route in apps/web/src/routes ever defined, so every magic-link email
    landed on a 404. The SPA route is "/magic-link" with the token in the
    query string (see $locale.magic-link.tsx), now locale-prefixed."""
    verified_user.locale = "de"
    session.add(verified_user)
    await session.flush()

    await service.request_magic_link(verified_user.email)

    kwargs = next(k for f, k in enqueued_jobs if f == "send_email")
    login_url: str = kwargs["context"]["login_url"]
    assert "/de/magic-link?token=" in login_url
    assert "/magic-link/consume" not in login_url


# --- the magic-link MFA bypass -----------------------------------------------------------
#
# A magic link proves control of an inbox. For an account whose owner
# enrolled a second factor that is one factor, not two -- but this path
# called _create_session directly, so the link alone produced a full session
# and the enrolled factor was never asked for. Password login had always
# challenged; three of the four sign-in paths disagreed with it.


async def test_a_magic_link_does_not_bypass_an_enrolled_authenticator(
    service: AuthService,
    verified_user: User,
    magic_link_repo: MagicLinkTokenRepository,
    session: AsyncSession,
) -> None:
    verified_user.totp_enabled = True
    await session.commit()
    plain = await _issue_magic_link_token(magic_link_repo, verified_user)

    result = await service.consume_magic_link(plain, _IP, _USER_AGENT)

    assert isinstance(result, TwoFactorChallenge)
    assert result.temp_token


async def test_a_magic_link_does_not_bypass_an_enrolled_security_key(
    service: AuthService,
    verified_user: User,
    magic_link_repo: MagicLinkTokenRepository,
    session: AsyncSession,
) -> None:
    # The half SSO also got wrong: it tested `totp_enabled` directly, so a
    # user whose only second factor was a security key was never challenged.
    session.add(
        WebAuthnCredential(
            user_id=verified_user.id,
            credential_id=f"cred-{uuid4()}",
            public_key="cHVibGljLWtleQ==",
            sign_count=0,
            aaguid="00000000-0000-0000-0000-000000000000",
            nickname="YubiKey",
        )
    )
    await session.commit()
    plain = await _issue_magic_link_token(magic_link_repo, verified_user)

    result = await service.consume_magic_link(plain, _IP, _USER_AGENT)

    assert isinstance(result, TwoFactorChallenge)


async def test_the_link_is_still_spent_when_a_second_factor_is_owed(
    service: AuthService,
    verified_user: User,
    magic_link_repo: MagicLinkTokenRepository,
    session: AsyncSession,
) -> None:
    """Single-use means single-use, challenge or not.

    The token proved control of the inbox, which is all it was ever good
    for. Leaving it unspent because the sign-in was not finished would make
    it replayable for its whole TTL.
    """
    verified_user.totp_enabled = True
    await session.commit()
    plain = await _issue_magic_link_token(magic_link_repo, verified_user)

    assert isinstance(await service.consume_magic_link(plain, _IP, _USER_AGENT), TwoFactorChallenge)

    with pytest.raises(AuthenticationError):
        await service.consume_magic_link(plain, _IP, _USER_AGENT)


async def test_no_session_exists_until_the_second_factor_is_cleared(
    service: AuthService,
    verified_user: User,
    magic_link_repo: MagicLinkTokenRepository,
    session_repo: SessionRepository,
    session: AsyncSession,
) -> None:
    # The assertion that would have caught the original bug: a challenge is
    # not a session, and nothing signed-in should exist yet.
    verified_user.totp_enabled = True
    await session.commit()
    plain = await _issue_magic_link_token(magic_link_repo, verified_user)

    await service.consume_magic_link(plain, _IP, _USER_AGENT)

    assert await session_repo.list_active_for_user(verified_user.id) == []
