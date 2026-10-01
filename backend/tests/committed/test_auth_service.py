"""
Verifies T-017 (auth service — register + verify email): registration never
reveals whether an email already exists, and verification tokens are
single-use and time-bound. Uses the same real-Postgres testcontainers
pattern as test_auth_repository.py.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from app.core.config import get_settings
from app.core.exceptions import AuthenticationError
from app.modules.audit.models import AuditEvent
from app.modules.auth.models import EmailVerificationToken, User
from app.modules.auth.repository import EmailVerificationTokenRepository, UserRepository
from app.modules.auth.schemas import RegisterRequest
from app.modules.auth.service import AuthService
from app.modules.auth.utils import hash_token

_VALID_PASSWORD = "Correct-Horse9"


@pytest.fixture
def service(session: AsyncSession) -> AuthService:
    return AuthService(session)


@pytest.fixture
def user_repo(session: AsyncSession) -> UserRepository:
    return UserRepository(session)


@pytest.fixture
def token_repo(session: AsyncSession) -> EmailVerificationTokenRepository:
    return EmailVerificationTokenRepository(session)


async def _issue_token(
    token_repo: EmailVerificationTokenRepository,
    user: User,
    *,
    plain: str = "plaintext-test-token",
    expires_at: datetime | None = None,
) -> str:
    """Mirrors what AuthService.register stores, but hands back the plain token directly."""
    await token_repo.create(
        user_id=user.id,
        token_hash=hash_token(plain),
        expires_at=expires_at or datetime.now(UTC) + timedelta(hours=24),
    )
    return plain


# --- register ----------------------------------------------------------------


async def test_register_creates_inactive_unverified_user(
    service: AuthService, user_repo: UserRepository
) -> None:
    await service.register(RegisterRequest(email="alice@example.com", password=_VALID_PASSWORD))

    created = await user_repo.get_by_email("alice@example.com")
    assert created is not None
    assert created.is_active is False
    assert created.email_verified_at is None
    assert created.hashed_password != _VALID_PASSWORD


async def test_register_creates_verification_token_for_new_user(
    service: AuthService, user_repo: UserRepository, session: AsyncSession
) -> None:
    await service.register(RegisterRequest(email="bob@example.com", password=_VALID_PASSWORD))

    user = await user_repo.get_by_email("bob@example.com")
    assert user is not None
    token_count = (
        await session.execute(
            select(func.count())
            .select_from(EmailVerificationToken)
            .where(EmailVerificationToken.user_id == user.id)
        )
    ).scalar_one()
    assert token_count == 1


async def test_register_verification_link_is_locale_prefixed(
    service: AuthService,
    enqueued_jobs: list[tuple[str, Any]],
) -> None:
    """T-233: every link into the web app carries a locale segment. A brand-new
    registrant has no stored preference yet, so this is the product default --
    what matters is that the segment is there at all, since
    apps/web/src/routes/$locale.tsx is what serves the page it points at."""
    await service.register(RegisterRequest(email="carol@example.com", password=_VALID_PASSWORD))

    kwargs = next(k for f, k in enqueued_jobs if f == "send_email")
    assert kwargs["template"] == "verify_email"
    assert kwargs["context"]["verification_url"].startswith(
        f"{get_settings().FRONTEND_URL}/en/verify-email?token="
    )


async def test_register_writes_audit_event(
    service: AuthService, user_repo: UserRepository, session: AsyncSession
) -> None:
    await service.register(RegisterRequest(email="audit@example.com", password=_VALID_PASSWORD))

    user = await user_repo.get_by_email("audit@example.com")
    assert user is not None
    event = (
        await session.execute(select(AuditEvent).where(AuditEvent.action == "user.registered"))
    ).scalar_one()
    assert event.actor_id == user.id
    assert event.resource_id == str(user.id)
    assert event.org_id is None


async def test_register_duplicate_email_does_not_raise(service: AuthService) -> None:
    req = RegisterRequest(email="carol@example.com", password=_VALID_PASSWORD)
    await service.register(req)

    result = await service.register(req)

    assert result is None


async def test_register_duplicate_email_does_not_create_second_user(
    service: AuthService, session: AsyncSession
) -> None:
    req = RegisterRequest(email="dave@example.com", password=_VALID_PASSWORD)
    await service.register(req)

    await service.register(req)

    count = (
        await session.execute(
            select(func.count()).select_from(User).where(User.email == "dave@example.com")
        )
    ).scalar_one()
    assert count == 1


# --- verify_email --------------------------------------------------------------


async def test_verify_email_marks_user_active_and_verified(
    service: AuthService, user_repo: UserRepository, token_repo: EmailVerificationTokenRepository
) -> None:
    user = await user_repo.create(
        email="erin@example.com", hashed_password="hashed", is_active=False
    )
    plain = await _issue_token(token_repo, user)

    verified = await service.verify_email(plain)

    assert verified.id == user.id
    assert verified.is_active is True
    assert verified.email_verified_at is not None


async def test_verify_email_with_unknown_token_raises(service: AuthService) -> None:
    with pytest.raises(AuthenticationError):
        await service.verify_email("not-a-real-token")


async def test_verify_email_with_expired_token_raises(
    service: AuthService, user_repo: UserRepository, token_repo: EmailVerificationTokenRepository
) -> None:
    user = await user_repo.create(email="frank@example.com", hashed_password="hashed")
    plain = await _issue_token(
        token_repo,
        user,
        plain="expired-plain-token",
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )

    with pytest.raises(AuthenticationError):
        await service.verify_email(plain)


async def test_verify_email_same_token_twice_raises_on_second_call(
    service: AuthService, user_repo: UserRepository, token_repo: EmailVerificationTokenRepository
) -> None:
    user = await user_repo.create(email="grace@example.com", hashed_password="hashed")
    plain = await _issue_token(token_repo, user)

    await service.verify_email(plain)

    with pytest.raises(AuthenticationError):
        await service.verify_email(plain)


async def test_verify_email_for_soft_deleted_user_raises(
    service: AuthService,
    user_repo: UserRepository,
    token_repo: EmailVerificationTokenRepository,
    session: AsyncSession,
) -> None:
    user = await user_repo.create(email="heidi@example.com", hashed_password="hashed")
    plain = await _issue_token(token_repo, user)
    user.deleted_at = datetime.now(UTC)
    await session.flush()

    with pytest.raises(AuthenticationError):
        await service.verify_email(plain)
