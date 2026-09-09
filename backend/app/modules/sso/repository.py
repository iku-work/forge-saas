"""
DB access for the `enterprise_sso` module (T-331) -- the only layer allowed
to build queries (§0.1).

Two repositories, split on whether the caller has an organization yet:

  * `SsoConnectionRepository` is the ordinary org-scoped one, for an admin
    managing their own org's connection.
  * `SsoConnectionLookupRepository` is **not** org-scoped, and cannot be: the
    login path resolves a connection *from* an org slug or an email domain,
    which is what it has before anybody is authenticated. Same shape, and the
    same reason, as `ApiKeyLookupRepository` (T-310).

The second one is where the care goes. Every method on it is reachable by an
unauthenticated visitor, so each returns at most one row, filters on
`deleted_at IS NULL`, and never leaks a row's existence through anything but
its own documented response.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select

from app.core.repository import BaseRepository
from app.modules.tenancy.models import Organization

from .models import SsoConnection

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


class SsoConnectionRepository(BaseRepository[SsoConnection]):
    model = SsoConnection

    async def get_for_org(self) -> SsoConnection | None:
        """This organization's connection, or None. There is at most one --
        the table's UNIQUE(org_id) says so."""
        m = cast("Any", SsoConnection)
        stmt = select(SsoConnection).where(m.org_id == self.org_id, m.deleted_at.is_(None))
        return (await self.session.execute(stmt)).scalars().first()


class SsoConnectionLookupRepository:
    """The login path's reads. Not org-scoped -- see the module docstring."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_org_slug(self, slug: str) -> tuple[Organization, SsoConnection] | None:
        """The org and its connection, for `/auth/sso/{org_slug}/start`.

        Returns the organization too because the caller needs it anyway (to
        build the audit event and to scope the membership), and fetching it
        here makes the whole resolution one statement instead of two.
        """
        o = cast("Any", Organization)
        c = cast("Any", SsoConnection)
        stmt = (
            select(Organization, SsoConnection)
            .join(SsoConnection, c.org_id == o.id)
            .where(o.slug == slug, o.deleted_at.is_(None), c.deleted_at.is_(None))
        )
        row = (await self._session.execute(stmt)).first()
        if row is None:
            return None
        return row[0], row[1]

    async def get_by_email_domain(self, domain: str) -> tuple[Organization, SsoConnection] | None:
        """The connection that claims an email domain, for the login page's
        opt-in hint.

        Filtered on `domain_hint_enabled` *here* rather than at the call
        site, so the one query that can leak "this domain has an account"
        cannot be written without the flag that decides whether it may.
        """
        o = cast("Any", Organization)
        c = cast("Any", SsoConnection)
        stmt = (
            select(Organization, SsoConnection)
            .join(SsoConnection, c.org_id == o.id)
            .where(
                c.email_domain == domain.strip().lower(),
                c.enabled.is_(True),
                c.domain_hint_enabled.is_(True),
                c.deleted_at.is_(None),
                o.deleted_at.is_(None),
            )
        )
        row = (await self._session.execute(stmt)).first()
        if row is None:
            return None
        return row[0], row[1]

    async def list_enforced_for_user(self, user_id: UUID) -> list[SsoConnection]:
        """Every enabled, enforced connection belonging to an organization
        this user is a member of.

        This is the query on the *password* login path, so it is written to
        be cheap and to be skipped entirely when the module is off (see
        `enforcement.password_login_blocked`). It joins through membership
        rather than taking an org id, because the question is "may this
        person use a password at all", which is not asked about one org.
        """
        # Imported here rather than at module scope: `app.modules.tenancy`
        # models are always loaded, but keeping the import local matches how
        # the rest of this file reads and avoids a second name at the top
        # that only one method uses.
        from app.modules.tenancy.models import Membership  # noqa: PLC0415

        m = cast("Any", Membership)
        c = cast("Any", SsoConnection)
        stmt = (
            select(SsoConnection)
            .join(Membership, m.org_id == c.org_id)
            .where(
                m.user_id == user_id,
                c.enabled.is_(True),
                c.enforced.is_(True),
                c.deleted_at.is_(None),
            )
        )
        return list((await self._session.execute(stmt)).scalars().all())
