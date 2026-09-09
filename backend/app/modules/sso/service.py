"""
Business logic for the `enterprise_sso` module (T-331).

Two services, and the split is the same one T-320 made: configuration and
the thing configuration enables.

  * **`SsoConnectionService`** -- an org admin's view. Create, read, update
    and delete the one connection an organization has, validating it against
    the IdP's own discovery document at save time so a typo is a 422 in a
    form rather than a locked-out organization at 9am.
  * **`SsoLoginService`** -- the flow. Start, callback, and the identity
    decisions in between: does this IdP get to assert this email, is this
    person already here, may they be created, what role do they get.

Nothing here commits; the router owns the transaction boundary.

## The one rule that makes per-org SSO safe

**An IdP may only assert identities in its own configured `email_domain`.**
Without that, a connection for `acme.com` -- created by an Acme admin, who is
not trusted with anybody else's data -- could return `email: ceo@rival.test`
and this product would hand it that account. The check is in
`_reject_foreign_domain`, before any lookup, and it is why `email_domain` is
a required column rather than a convenience for the hint endpoint.

## Session issuance goes through AuthService

Not because it is tidy, but because every other property of a session in this
product -- the refresh-token chain, `auth_time`, the audit event, the 2FA
gate -- lives there. A second place that mints sessions is a second place to
forget one of them. `AuthService.sso_login` is the entry point, and it is the
one that applies TOTP when the IdP did *not* assert MFA.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Any
from uuid import UUID

from loguru import logger

from app.core.exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    UnprocessableEntityError,
)
from app.models._mixins import utcnow
from app.modules.audit.service import AuditService
from app.modules.auth.permissions import MemberRole
from app.modules.auth.repository import UserRepository
from app.modules.auth.service import AuthService
from app.modules.auth.utils import sign_url_token, verify_url_token
from app.modules.tenancy.repository import MembershipRepository

from . import discovery as discovery_module
from . import oidc
from .models import SsoConnection
from .repository import SsoConnectionLookupRepository, SsoConnectionRepository

if TYPE_CHECKING:
    import httpx
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.modules.auth.models import User
    from app.modules.auth.service import TwoFactorChallenge
    from app.modules.tenancy.models import Organization

    from .schemas import SsoConnectionUpsertRequest

RESOURCE_TYPE = "sso_connection"

# How long a browser has between `/start` and `/callback`. Ten minutes is the
# same window T-023's OAuth state uses; an IdP login that takes longer than
# that has usually been abandoned in a tab.
STATE_TTL_SECONDS = 600

# The `provider` an SSO-linked OAuthAccount is recorded under. Namespaced so
# it can never collide with a social provider (`google`, `github`), and
# scoped by *connection id* rather than by org slug -- a slug can be renamed,
# and a link that survives a rename is the point of storing an id.
SSO_PROVIDER_PREFIX = "sso:"


def sso_provider_name(connection_id: UUID) -> str:
    return f"{SSO_PROVIDER_PREFIX}{connection_id}"


class SsoConnectionService:
    """Managing one organization's IdP. Org-scoped, like the repository."""

    def __init__(self, session: AsyncSession, org_id: UUID, redis: Redis) -> None:
        self._session = session
        self._org_id = org_id
        self._redis = redis
        self._repo = SsoConnectionRepository(session, org_id)
        self._audit = AuditService(session)

    async def get(self) -> SsoConnection | None:
        return await self._repo.get_for_org()

    async def upsert(self, user_id: UUID, payload: SsoConnectionUpsertRequest) -> SsoConnection:
        """Create or replace this org's connection, after proving it works.

        "After proving it works" is the whole reason this is not a plain
        write: the discovery document is fetched and its `issuer` compared
        against the one the admin typed. An issuer that disagrees is the
        single most common SSO misconfiguration, and it fails at *login* --
        for everyone, at once -- unless something checks it here.
        """
        discovery = await discovery_module.fetch_discovery(
            self._redis, payload.discovery_url, force=True
        )
        if discovery["issuer"] != payload.issuer:
            raise UnprocessableEntityError(
                "errors.sso_issuer_mismatch",
                fallback=(
                    f"The discovery document declares issuer {discovery['issuer']!r}, "
                    f"not {payload.issuer!r}"
                ),
                params={"declared": str(discovery["issuer"]), "configured": payload.issuer},
            )

        fields: dict[str, Any] = payload.model_dump()
        fields["group_role_mappings"] = {
            group: role.value for group, role in payload.group_role_mappings.items()
        }
        existing = await self._repo.get_for_org()
        if existing is None:
            connection = await self._repo.create(**fields)
            action = "sso_connection.created"
        else:
            # Bust the old URLs' caches before the row moves: after the
            # update this service can no longer name them, and a stale
            # discovery document for a *replaced* IdP is the worst kind --
            # it works, against the wrong provider.
            await discovery_module.invalidate(
                self._redis, existing.discovery_url, str(discovery.get("jwks_uri") or "")
            )
            connection = await self._repo.update(existing.id, **fields)
            action = "sso_connection.updated"

        await self._log(action, user_id, connection.id, diff=_auditable(fields))
        return connection

    async def delete(self, user_id: UUID) -> None:
        connection = await self._repo.get_for_org()
        if connection is None:
            raise NotFoundError(
                "errors.sso_not_configured",
                fallback="This organization has no SSO connection",
            )
        if connection.enforced:
            # Deleting an enforced connection is the one order of operations
            # that strands people: enforcement outlives the connection for as
            # long as the row is being deleted, and a member with no password
            # has nothing left. Turning enforcement off first is one extra
            # request and removes the whole class of incident.
            raise ConflictError(
                "errors.sso_delete_while_enforced",
                fallback="Turn enforcement off before deleting this connection",
            )
        await discovery_module.invalidate(self._redis, connection.discovery_url, None)
        await self._repo.soft_delete(connection.id)
        await self._log("sso_connection.deleted", user_id, connection.id)

    async def _log(
        self,
        action: str,
        user_id: UUID | None,
        connection_id: UUID,
        diff: dict[str, Any] | None = None,
    ) -> None:
        await self._audit.log(
            action=action,
            resource_type=RESOURCE_TYPE,
            resource_id=str(connection_id),
            org_id=self._org_id,
            actor_id=user_id,
            diff=diff,
        )


def _auditable(fields: dict[str, Any]) -> dict[str, Any]:
    """Everything about a connection change except the credential.

    An audit feed is read by more people than a database is -- the same
    sentence T-310 wrote about API keys, and the same rule.
    """
    return {
        key: (value.value if isinstance(value, MemberRole) else value)
        for key, value in fields.items()
        if key != "client_secret"
    }


class SsoLoginService:
    """The redirect flow. Not org-scoped -- it resolves the org itself."""

    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self._session = session
        self._redis = redis
        self._lookup = SsoConnectionLookupRepository(session)
        self._users = UserRepository(session)
        self._memberships = MembershipRepository(session)
        self._audit = AuditService(session)

    async def start(self, org_slug: str, *, redirect_uri: str) -> tuple[str, str]:
        """`(authorization_url, state)` for an organization's IdP.

        The `state` is an itsdangerous-signed token carrying the connection
        id and the nonce, and the router mirrors it in a short-lived cookie —
        the same two-part CSRF defence T-023's OAuth flow uses, for the same
        reason (this app has no server-side session to hold it in).
        """
        resolved = await self._lookup.get_by_org_slug(org_slug)
        if resolved is None or not resolved[1].enabled:
            # 404 for both "no such org" and "no SSO here": an unauthenticated
            # caller must not be able to enumerate organizations by slug.
            raise NotFoundError(
                "errors.sso_not_configured",
                fallback="Single sign-on is not available for this organization",
            )
        _organization, connection = resolved
        discovery = await discovery_module.fetch_discovery(self._redis, connection.discovery_url)
        nonce = secrets.token_urlsafe(24)
        state = sign_url_token(
            {"connection_id": str(connection.id), "nonce": nonce}, STATE_TTL_SECONDS
        )
        url = oidc.authorization_url(
            discovery, connection, redirect_uri=redirect_uri, state=state, nonce=nonce
        )
        return url, state

    async def complete(  # noqa: PLR0913 -- the callback's own inputs, all required
        self,
        *,
        state: str,
        code: str,
        redirect_uri: str,
        ip: str,
        user_agent: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> tuple[str, str, User] | TwoFactorChallenge:
        """Everything after the IdP redirects back."""
        payload = verify_url_token(state, max_age=STATE_TTL_SECONDS)
        connection_id = payload.get("connection_id")
        nonce = payload.get("nonce")
        if not isinstance(connection_id, str) or not isinstance(nonce, str):
            raise AuthenticationError(
                "errors.sso_bad_state", fallback="This sign-in link is not valid"
            )

        connection = await self._session.get(SsoConnection, UUID(connection_id))
        if connection is None or connection.deleted_at is not None or not connection.enabled:
            raise AuthenticationError(
                "errors.sso_not_configured",
                fallback="Single sign-on is not available for this organization",
            )
        organization = await self._organization(connection.org_id)

        discovery = await discovery_module.fetch_discovery(self._redis, connection.discovery_url)
        id_token = await oidc.exchange_code(
            discovery, connection, code=code, redirect_uri=redirect_uri, transport=transport
        )
        identity = await oidc.verify_id_token(
            self._redis, discovery, connection, id_token=id_token, nonce=nonce
        )
        self._reject_foreign_domain(connection, identity.email)

        user = await self._resolve_user(connection, organization, identity)
        return await AuthService(self._session).sso_login(
            user,
            ip,
            user_agent,
            provider=sso_provider_name(connection.id),
            provider_user_id=identity.subject,
            mfa_asserted=identity.asserted_mfa,
        )

    async def hint(self, email: str) -> tuple[Organization, SsoConnection] | None:
        """The opt-in domain hint. None when there is nothing to say."""
        _, _, domain = email.strip().lower().rpartition("@")
        if not domain:
            return None
        return await self._lookup.get_by_email_domain(domain)

    def _reject_foreign_domain(self, connection: SsoConnection, email: str) -> None:
        """The check that makes per-org SSO safe -- see the module docstring.

        Compared against the *stored* domain rather than against the email's
        organization membership, because the whole point is to constrain an
        IdP before this product looks anything up.
        """
        _, _, domain = email.rpartition("@")
        if domain != connection.email_domain:
            logger.warning(
                "sso.foreign_domain_rejected: connection_id={} claimed_domain={} allowed={}",
                connection.id,
                domain,
                connection.email_domain,
            )
            raise AuthenticationError(
                "errors.sso_foreign_domain",
                fallback=(
                    f"This identity provider may only sign in @{connection.email_domain} addresses"
                ),
                params={"domain": connection.email_domain},
            )

    async def _resolve_user(
        self,
        connection: SsoConnection,
        organization: Organization,
        identity: oidc.SsoIdentity,
    ) -> User:
        """The person behind an asserted identity: found, or provisioned."""
        user = await self._users.get_by_email(identity.email)
        if user is None:
            if not connection.jit_provisioning:
                raise AuthenticationError(
                    "errors.sso_no_account",
                    fallback="No account here matches that identity, and this "
                    "organization does not create them automatically",
                )
            user = await self._users.create(
                email=identity.email,
                # No password, ever. A JIT-provisioned account whose only
                # credential is the IdP is exactly what enforcement assumes;
                # giving it a null password is what makes that true.
                hashed_password=None,
                # The IdP authenticated them, which is a stronger statement
                # than an email round trip -- and the domain check above
                # already proved the address is one this IdP owns.
                email_verified_at=utcnow(),
                is_active=True,
                full_name=identity.full_name,
            )
            await self._audit.log(
                action="sso.user_provisioned",
                resource_type="user",
                resource_id=str(user.id),
                org_id=organization.id,
                actor_id=user.id,
            )

        await self._ensure_membership(connection, organization, user, identity)
        return user

    async def _ensure_membership(
        self,
        connection: SsoConnection,
        organization: Organization,
        user: User,
        identity: oidc.SsoIdentity,
    ) -> None:
        """Membership of the org whose IdP just vouched for this person.

        Created at first sign-in; **never re-graded afterwards.** ADR-0006
        decision 6 is explicit that continuous group reconciliation is not in
        this task -- demoting somebody because a group changed needs to react
        to the change, and the mechanism for that is SCIM. Silently
        re-applying the mapping on every login would be a half-version of it
        that also overwrites a role an admin set by hand.
        """
        existing = await self._memberships.get_by_user_and_org(user.id, organization.id)
        if existing is not None:
            return
        if not connection.jit_provisioning:
            raise AuthenticationError(
                "errors.sso_not_a_member",
                fallback="That identity is not a member of this organization",
            )
        role = _role_for(connection, identity.groups)
        await self._memberships.create(user_id=user.id, org_id=organization.id, role=role)
        await self._audit.log(
            action="sso.membership_provisioned",
            resource_type="membership",
            resource_id=str(user.id),
            org_id=organization.id,
            actor_id=user.id,
            diff={"role": role.value, "groups": list(identity.groups)},
        )

    async def _organization(self, org_id: UUID) -> Organization:
        from app.modules.tenancy.models import Organization as OrganizationModel  # noqa: PLC0415

        organization = await self._session.get(OrganizationModel, org_id)
        if organization is None:  # pragma: no cover -- FK guarantees a row
            raise NotFoundError("errors.org_not_found", fallback="Organization not found")
        return organization


def _role_for(connection: SsoConnection, groups: tuple[str, ...]) -> MemberRole:
    """The role a JIT-provisioned member gets.

    The mapping wins over the default when a group matches; `owner` is
    refused even if somebody put it in the mapping, for the same reason
    `default_role` refuses it in the request schema -- an IdP says who
    somebody is, not what they may do here. The first matching group wins, in
    the order the IdP listed them, so the answer does not depend on dict
    ordering.
    """
    for group in groups:
        mapped = connection.group_role_mappings.get(group)
        if mapped is None:
            continue
        try:
            role = MemberRole(mapped)
        except ValueError:  # pragma: no cover -- the schema validates on write
            continue
        if role is not MemberRole.OWNER:
            return role
    return connection.default_role
