"""
Anyone can register any address, so the password on an unverified account says
nothing about who will eventually prove they own the inbox. Each test is one
way an attacker's password could have survived that (review 2026-11-02, H1).
"""

from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthenticationError
from app.modules.auth.models import User
from app.modules.auth.repository import UserRepository
from app.modules.auth.schemas import RegisterRequest
from app.modules.auth.service import AuthService
from app.modules.auth.utils import verify_password

_EMAIL = "victim@example.com"
_ATTACKER_PASSWORD = "Correct-Horse9"
_OWNER_PASSWORD = "Even-Better-Horse8"
_RESET_PASSWORD = "Fresh-Start7-Correct"


def _token_from_email(jobs: list[tuple[str, dict[str, Any]]], template: str, url_key: str) -> str:
    """The token in the newest email of this template."""
    context = [k for f, k in jobs if f == "send_email" and k["template"] == template][-1]["context"]
    return parse_qs(urlparse(context[url_key]).query)["token"][0]


@pytest.fixture
def service(session: AsyncSession) -> AuthService:
    return AuthService(session)


@pytest.fixture
async def attacker_registered(service: AuthService) -> None:
    await service.register(RegisterRequest(email=_EMAIL, password=_ATTACKER_PASSWORD))


async def _account(session: AsyncSession) -> User:
    user = await UserRepository(session).get_by_email(_EMAIL)
    assert user is not None
    return user


@pytest.mark.usefixtures("attacker_registered")
async def test_a_link_activates_the_password_it_was_sent_with(
    service: AuthService,
    session: AsyncSession,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    attackers_link = _token_from_email(enqueued_jobs, "verify_email", "verification_url")
    await service.register(RegisterRequest(email=_EMAIL, password=_OWNER_PASSWORD))
    owners_link = _token_from_email(enqueued_jobs, "verify_email", "verification_url")

    # The attacker registers again after the owner. It must not change what
    # the owner's link does.
    await service.register(RegisterRequest(email=_EMAIL, password=_ATTACKER_PASSWORD))
    await service.verify_email(owners_link)

    user = await _account(session)
    assert user.is_active is True
    assert user.hashed_password is not None
    assert verify_password(_OWNER_PASSWORD, user.hashed_password)
    assert not verify_password(_ATTACKER_PASSWORD, user.hashed_password)

    # And the links still in that inbox cannot bring the attacker's back.
    with pytest.raises(AuthenticationError):
        await service.verify_email(attackers_link)


@pytest.mark.usefixtures("attacker_registered")
async def test_a_magic_link_removes_the_password(
    service: AuthService,
    session: AsyncSession,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    pending_link = _token_from_email(enqueued_jobs, "verify_email", "verification_url")
    await service.request_magic_link(_EMAIL)

    await service.consume_magic_link(
        _token_from_email(enqueued_jobs, "magic_link", "login_url"), "127.0.0.1", "pytest"
    )

    user = await _account(session)
    assert user.is_active is True
    assert user.email_verified_at is not None
    assert user.hashed_password is None
    with pytest.raises(AuthenticationError):
        await service.verify_email(pending_link)


@pytest.mark.usefixtures("attacker_registered")
async def test_a_password_reset_outranks_a_pending_link(
    service: AuthService,
    session: AsyncSession,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    pending_link = _token_from_email(enqueued_jobs, "verify_email", "verification_url")
    await service.request_password_reset(_EMAIL)

    await service.confirm_password_reset(
        _token_from_email(enqueued_jobs, "password_reset", "reset_url"), _RESET_PASSWORD
    )

    user = await _account(session)
    assert user.email_verified_at is not None
    assert user.hashed_password is not None
    assert verify_password(_RESET_PASSWORD, user.hashed_password)
    with pytest.raises(AuthenticationError):
        await service.verify_email(pending_link)
