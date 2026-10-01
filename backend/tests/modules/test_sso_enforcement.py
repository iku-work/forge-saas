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

    **And `oauth` with it**, because `enterprise_sso` requires it and
    `validate_modules` refuses the pair otherwise. Enabling only the one this
    file is about read fine against the committed config, where `oauth`
    happens to be on -- and blew up in CI's `module-matrix` job on the
    everything-off leg, which is the configuration this fixture has no right
    to assume anything about. A fixture that turns a module on owes it its
    dependencies; the refusal itself is asserted in test_module_toggles.py.
    """
    overridden = replace(
        product_config.MODULES,
        enterprise_sso=replace(product_config.MODULES.enterprise_sso, enabled=True),
        oauth=replace(product_config.MODULES.oauth, enabled=True),
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
    # Committed, not flushed: a refused login rolls the session back before
    # it records the refusal (persist_then_reject), and in production this
    # user would have been committed by an earlier request long before.
    await session.commit()
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
    await session.commit()
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
    slug = org.slug  # read now: the refusal's rollback expires `org`

    with pytest.raises(PermissionDeniedError) as caught:
        await _login(session, member.email)

    assert caught.value.message_key == "errors.sso_required"
    # The message names the provider and the entry point, because a refusal
    # a person cannot act on is a support ticket.
    assert caught.value.params["provider"] == "Acme IdP"
    assert caught.value.params["org_slug"] == slug


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
    await session.commit()

    with pytest.raises(PermissionDeniedError):
        await _login(session, user.email)


@pytest.mark.usefixtures("sso_enabled")
async def test_a_refusal_is_audited_and_nothing_else_is_kept(session: AsyncSession) -> None:
    """The refusal is written down; the rest of the refused login is not.

    By the time enforcement refuses, login has already reset the user's
    failed-login counter (the password was right). That reset belongs to a
    login that did not happen, so it must not survive the refusal -- which
    is the difference between persist_then_reject and a plain
    commit-before-raise (review R2).
    """
    org = await _enforcing_org(session)
    await _member(session, org, MemberRole.OWNER)
    await _member(session, org, MemberRole.OWNER)
    member = await _member(session, org, MemberRole.MEMBER)
    member_id, email = member.id, member.email
    await UserRepository(session).update(member_id, failed_login_count=3)
    await session.commit()

    with pytest.raises(PermissionDeniedError):
        await _login(session, email)

    actions = (
        (await session.execute(select(AuditEvent.action).where(AuditEvent.actor_id == member_id)))
        .scalars()
        .all()
    )
    assert "user.password_login_refused_sso" in actions
    refreshed = await UserRepository(session).get_by_id(member_id)
    assert refreshed is not None
    assert refreshed.failed_login_count == 3


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
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An enforcing connection is ignored outright while the module is off,
    which is what makes this check acceptable on every password login in the
    product.

    The module is turned off *here* rather than left to the ambient config.
    Relying on "it is off in the committed configuration" made this test pass
    for the wrong reason and then fail outright in CI's module-matrix job on
    the Phase-4-modules-on leg, where it is on -- a test named for a state it
    did not establish.
    """
    overridden = replace(
        product_config.MODULES,
        enterprise_sso=replace(product_config.MODULES.enterprise_sso, enabled=False),
    )
    monkeypatch.setattr(product_config, "MODULES", overridden)
    module_loader.get_modules.cache_clear()

    org = await _enforcing_org(session)
    await _member(session, org, MemberRole.OWNER)
    member = await _member(session, org, MemberRole.MEMBER)

    result = await _login(session, member.email)

    assert result[0]  # type: ignore[index]
