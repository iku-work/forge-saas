"""
"This organization signs in with SSO, so a password will not do" — and the
one exception that keeps it from being a trap (T-331).

`AuthService.login` calls `password_login_blocked` after it has verified the
password and before it issues anything. That ordering is deliberate: an
enforcement check *before* the password check would answer "is this address
in an SSO organization" to anyone who can type an email, which is the same
leak `domain_hint_enabled` exists to make opt-in.

## The break-glass

An organization that enforces SSO against a misconfigured IdP has locked
every one of its people out, and the only fix is somebody with a psql prompt.
So **the last remaining owner can always use a password.** Not any owner —
the *last* one: an org with two owners has a second way in, and the escape
hatch should be as narrow as it can be while still being an escape hatch.
ADR-0006 decision 8 records this, and gives the reasoning in one line: the
safety valve is worth more than the purity.

This module lives outside `service.py` on purpose. `app.modules.auth.service`
imports it, and `app.modules.sso.service` imports `app.modules.auth.service`
— putting the check in the service would close that loop. Nothing here
imports anything from this module's own service layer, and that is what keeps
it open.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select

from app.core.module_loader import is_enabled
from app.modules.auth.permissions import MemberRole
from app.modules.tenancy.models import Membership

from .repository import SsoConnectionLookupRepository

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class SsoRequired:
    """Why a password was refused, in the terms the login page needs to act:
    where to send the person instead."""

    org_slug: str
    provider_label: str


async def password_login_blocked(session: AsyncSession, user_id: UUID) -> SsoRequired | None:
    """The connection that refuses this user a password, or None.

    Returns None immediately when the module is off, so a product that never
    enables `enterprise_sso` pays nothing on its login path -- not a query,
    not a join. That matters more here than almost anywhere else in the
    codebase: this runs on every successful password check in the product.
    """
    if not is_enabled("enterprise_sso"):
        return None

    connections = await SsoConnectionLookupRepository(session).list_enforced_for_user(user_id)
    for connection in connections:
        if await _is_last_owner(session, user_id, connection.org_id):
            # Break-glass. Keep looking: this user may be the last owner of
            # one enforcing org and an ordinary member of another, and the
            # second one still refuses them.
            continue
        organization = await _org_slug(session, connection.org_id)
        if organization is None:  # pragma: no cover -- FK guarantees a row
            continue
        return SsoRequired(org_slug=organization, provider_label=connection.provider_label)
    return None


async def _is_last_owner(session: AsyncSession, user_id: UUID, org_id: UUID) -> bool:
    m = cast("Any", Membership)
    owner_ids = (
        (
            await session.execute(
                select(m.user_id).where(m.org_id == org_id, m.role == MemberRole.OWNER)
            )
        )
        .scalars()
        .all()
    )
    return list(owner_ids) == [user_id]


async def _org_slug(session: AsyncSession, org_id: UUID) -> str | None:
    from app.modules.tenancy.models import Organization  # noqa: PLC0415 -- one caller

    o = cast("Any", Organization)
    return (await session.execute(select(o.slug).where(o.id == org_id))).scalars().first()
