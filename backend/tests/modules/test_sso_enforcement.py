"""
"This organization signs in with SSO, so a password will not do" — and the
break-glass that stops it from being a trap (T-331).

Exercised through `AuthService.login` rather than through
`enforcement.password_login_blocked` directly, because the property that
matters is *where in the login sequence* the refusal happens: after the
password check, so the endpoint cannot be used to discover which addresses
belong to an SSO organization, and before any token is minted, so a refused
login leaves nothing behind.

`enterprise_sso` ships off, so every test here turns it on the way the rest
of the suite does — monkeypatching `product.config.MODULES` and clearing
`get_modules`' cache. The one test that leaves it off is the one asserting
the check costs nothing then.
"""

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlmodel import SQLModel

from app.core import module_loader
from app.core.exceptions import AuthenticationError, PermissionDeniedError
from app.modules.audit.models import AuditEvent
from app.modules.auth.permissions import MemberRole
from app.modules.auth.repository import UserRepository
from app.modules.auth.schemas import LoginRequest
from app.modules.auth.service import AuthService
from app.modules.auth.utils import hash_password
from app.modules.sso.models import SsoConnection
from app.modules.sso.repository import SsoConnectionRepository
from app.modules.tenancy.models import Organization
from app.modules.tenancy.repository import MembershipRepository, OrganizationRepository
from product import config as product_config

_PASSWORD = "Correct-Horse9"


@pytest.fixture(autouse=True)
async def _sso_table(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all, tables=[SsoConnection.__table__])


@pytest.fixture
def sso_enabled(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """`enterprise_sso` on, for the duration of one test.

    `enterprise_sso` requires `oauth`, which is already on in the committed
    config -- `validate_modules` would refuse the combination otherwise, and
    that refusal is asserted in test_module_toggles.py rather than worked
    around here.
    """
    overridden = replace(
        product_config.MODULES,
        enterprise_sso=replace(product_config.MODULES.enterprise_sso, enabled=True),
    )
    monkeypatch.setattr(product_config, "MODULES", overridden)
    module_loader.get_modules.cache_clear()
    yield
    module_loader.get_modules.cache_clear()


async def _member(
    session: AsyncSession, org: Organization, role: MemberRole, email: str | None = None
) -> Any:  # noqa: ANN401 -- the User model, imported lazily by the repository
    user = await UserRepository(session).create(
        email=email or f"{uuid4()}@acme.example",
        hashed_password=hash_password(_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    await MembershipRepository(session).create(user_id=user.id, org_id=org.id, role=role)
    await session.flush()
    return user


async def _enforcing_org(session: AsyncSession, **overrides: object) -> Organization:
    org = await OrganizationRepository(session).create(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    fields: dict[str, Any] = {
        "provider_label": "Acme IdP",
        "issuer": "https://idp.example.test",
        "discovery_url": "https://idp.example.test/.well-known/openid-configuration",
        "client_id": "forge-client",
        "client_secret": "s",
        "email_domain": "acme.example",
        "enforced": True,
        "enabled": True,
    }
    fields.update(overrides)
    await SsoConnectionRepository(session, org.id).create(**fields)
    await session.flush()
    return org


async def _login(session: AsyncSession, email: str) -> object:
    return await AuthService(session).login(
        LoginRequest(email=email, password=_PASSWORD), "1.2.3.4", "pytest"
    )


@pytest.mark.usefixtures("sso_enabled")
async def test_a_member_of_an_enforcing_org_cannot_use_a_password(
    session: AsyncSession,
) -> None:
    org = await _enforcing_org(session)
    # Two owners, so the break-glass does not apply to either of them.
    await _member(session, org, MemberRole.OWNER)
    await _member(session, org, MemberRole.OWNER)
    member = await _member(session, org, MemberRole.MEMBER)

    with pytest.raises(PermissionDeniedError) as caught:
        await _login(session, member.email)

    assert caught.value.message_key == "errors.sso_required"
    # The message names the provider and the entry point, because a refusal
    # a person cannot act on is a support ticket.
    assert caught.value.params["provider"] == "Acme IdP"
    assert caught.value.params["org_slug"] == org.slug


@pytest.mark.usefixtures("sso_enabled")
async def test_the_last_remaining_owner_keeps_a_password(
    session: AsyncSession,
) -> None:
    """The break-glass. Without it an organization can lock itself out
    permanently against a misconfigured IdP, and the fix is a DBA."""
    org = await _enforcing_org(session)
    owner = await _member(session, org, MemberRole.OWNER)

    result = await _login(session, owner.email)

    access_token, _refresh, user = result  # type: ignore[misc]
    assert access_token
    assert user.id == owner.id


@pytest.mark.usefixtures("sso_enabled")
async def test_one_of_two_owners_does_not_get_the_break_glass(
    session: AsyncSession,
) -> None:
    """*Last* remaining owner, not any owner -- an org with two owners has a
    second way back in, so the escape hatch stays as narrow as it can be."""
    org = await _enforcing_org(session)
    first = await _member(session, org, MemberRole.OWNER)
    await _member(session, org, MemberRole.OWNER)

    with pytest.raises(PermissionDeniedError):
        await _login(session, first.email)


@pytest.mark.usefixtures("sso_enabled")
async def test_being_the_last_owner_somewhere_does_not_excuse_you_elsewhere(
    session: AsyncSession,
) -> None:
    """The loop keeps looking after a break-glass match, and this is why."""
    own_org = await _enforcing_org(session)
    other_org = await _enforcing_org(session)
    user = await _member(session, own_org, MemberRole.OWNER)
    await MembershipRepository(session).create(
        user_id=user.id, org_id=other_org.id, role=MemberRole.MEMBER
    )
    # The second org has its own owner, so the user is not its last one.
    await _member(session, other_org, MemberRole.OWNER)
    await session.flush()

    with pytest.raises(PermissionDeniedError):
        await _login(session, user.email)


@pytest.mark.usefixtures("sso_enabled")
async def test_a_refusal_is_audited(session: AsyncSession) -> None:
    org = await _enforcing_org(session)
    await _member(session, org, MemberRole.OWNER)
    await _member(session, org, MemberRole.OWNER)
    member = await _member(session, org, MemberRole.MEMBER)

    with pytest.raises(PermissionDeniedError):
        await _login(session, member.email)

    actions = (
        (await session.execute(select(AuditEvent.action).where(AuditEvent.actor_id == member.id)))
        .scalars()
        .all()
    )
    assert "user.password_login_refused_sso" in actions


@pytest.mark.usefixtures("sso_enabled")
async def test_a_wrong_password_is_still_a_wrong_password(
    session: AsyncSession,
) -> None:
    """The refusal comes *after* the password check, so this endpoint cannot
    be used to discover which addresses belong to an SSO organization."""
    org = await _enforcing_org(session)
    await _member(session, org, MemberRole.OWNER)
    await _member(session, org, MemberRole.OWNER)
    member = await _member(session, org, MemberRole.MEMBER)

    with pytest.raises(AuthenticationError) as caught:
        await AuthService(session).login(
            LoginRequest(email=member.email, password="wrong-password-entirely"),
            "1.2.3.4",
            "pytest",
        )

    assert caught.value.message_key == "errors.invalid_credentials"


@pytest.mark.usefixtures("sso_enabled")
async def test_a_connection_that_is_not_enforced_changes_nothing(
    session: AsyncSession,
) -> None:
    org = await _enforcing_org(session, enforced=False)
    await _member(session, org, MemberRole.OWNER)
    member = await _member(session, org, MemberRole.MEMBER)

    result = await _login(session, member.email)

    assert result[0]  # type: ignore[index]


async def test_the_check_is_skipped_entirely_when_the_module_is_off(
    session: AsyncSession,
) -> None:
    """No `sso_enabled` fixture: at the committed configuration this must not
    even query, which is what makes it acceptable on every password login in
    the product."""
    org = await _enforcing_org(session)
    await _member(session, org, MemberRole.OWNER)
    member = await _member(session, org, MemberRole.MEMBER)

    result = await _login(session, member.email)

    assert result[0]  # type: ignore[index]
