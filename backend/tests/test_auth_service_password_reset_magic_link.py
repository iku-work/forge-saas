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
from app.modules.auth.models import MagicLinkToken, PasswordResetToken, User
from app.modules.auth.models import Session as SessionModel
from app.modules.auth.repository import (
    MagicLinkTokenRepository,
    PasswordResetTokenRepository,
    SessionRepository,
    UserRepository,
)
from app.modules.auth.service import AuthService
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
