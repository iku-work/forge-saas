"""
The `sso_connection` table (T-331): one organization's identity provider.

**One row per organization, and the unique constraint says so.** A global IdP
would mean one tenant's identity provider authenticating another tenant's
users, which is not a multi-tenant product — ADR-0006 records that as a
decision rather than leaving it to be inferred from a schema.

`client_secret` ends in `_secret`, so §7's Semgrep rule requires
`EncryptedString`, and it is a genuine requirement rather than a convention:
the token exchange has to *present* the secret to the IdP, so a digest would
make the column useless. Same reasoning as `webhook_endpoint.secret`
(T-320), opposite of `api_key.key_hash` (T-310). `RedactedReprMixin` keeps it
out of tracebacks and Sentry events.

`email_domain` is not decoration either — it is a **security boundary**. An
IdP is trusted to say who its own people are, and nothing more: a connection
for `acme.com` that returns `email: ceo@rival.example` must not be able to
take over that account. `SsoLoginService` enforces the match before it looks
a user up, which is the one check that makes per-org SSO safe to offer to
tenants who are not trusted with each other's data.
"""

from typing import ClassVar
from uuid import UUID

from sqlalchemy import Column, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.core.encrypted_type import EncryptedString, RedactedReprMixin
from app.models._mixins import SoftDeleteMixin, TimestampMixin, UUIDPKMixin
from app.modules.auth.permissions import MemberRole
from app.modules.tenancy.models import member_role_column

# How long a fetched discovery document and its JWKS stay cached. Long enough
# that a login is one round trip to the IdP rather than three; short enough
# that a key rotation propagates within the hour without anyone intervening.
# `SsoConnectionService.update` busts both keys on every edit, so a *config*
# change is immediate and only a *key* change waits.
DISCOVERY_CACHE_TTL_SECONDS = 3600


class SsoConnection(
    RedactedReprMixin, UUIDPKMixin, TimestampMixin, SoftDeleteMixin, SQLModel, table=True
):
    __tablename__ = "sso_connection"
    __redacted_fields__: ClassVar[set[str]] = {"client_secret"}
    __table_args__: ClassVar[tuple[UniqueConstraint | Index, ...]] = (
        # One IdP per organization -- see the module docstring. Enforced by
        # the database rather than by the service, because "we only ever
        # create one" is a sentence that stops being true under a retry.
        UniqueConstraint("org_id", name="uq_sso_connection_org_id"),
        # The login path's only query: resolve a connection from the email
        # domain a visitor typed. Partial on `enabled` is tempting and wrong
        # -- a disabled connection still has to be *found* so the login page
        # can say "SSO is turned off for this domain" rather than nothing.
        Index("ix_sso_connection_email_domain", "email_domain"),
    )

    # Declared here rather than through OrgScopedMixin for the same reason
    # ApiKey declares its own: this one is UNIQUE, and the mixin's is not.
    org_id: UUID = Field(foreign_key="organization.id", index=True, ondelete="CASCADE")

    # What the login button says. "Okta", "Entra ID", "Acme SSO" -- the
    # organization's word for it, not ours, because it is their people who
    # read it.
    provider_label: str = Field(max_length=100)
    # The `iss` every ID token from this IdP must carry. Stored rather than
    # taken from the discovery document at validation time: the two must
    # agree, and comparing them is what makes a swapped discovery URL a
    # failure instead of a silent redirect to somebody else's IdP.
    issuer: str = Field(max_length=500)
    discovery_url: str = Field(max_length=500)
    client_id: str = Field(max_length=255)
    client_secret: str = Field(sa_column=Column(EncryptedString, nullable=False))

    # Lower-cased, no `@`. Every identity this connection may assert must end
    # in it -- see the module docstring.
    email_domain: str = Field(max_length=255)

    # A user the IdP authenticates who has no account here is created and
    # added to the organization. Off means "the IdP can only sign in people
    # who are already members", which is what an org that manages membership
    # by hand wants.
    jit_provisioning: bool = Field(default=True)
    default_role: MemberRole = Field(
        default=MemberRole.MEMBER, sa_column=Column(member_role_column(), nullable=False)
    )
    # IdP group name -> role in this product, applied at JIT provisioning
    # (ADR-0006 decision 6). Continuous reconciliation is deliberately not
    # implemented; the ADR says why, and says SCIM is the mechanism for it.
    group_claim: str | None = Field(default=None, max_length=100)
    group_role_mappings: dict[str, str] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False)
    )

    # Members of this org cannot use a password. The break-glass is in
    # app.modules.sso.enforcement, and it is not optional: an org can
    # otherwise lock itself out permanently against a misconfigured IdP and
    # the only fix is a DBA.
    enforced: bool = Field(default=False)
    # Whether typing an email at this domain reveals that SSO exists for it.
    # **Default off, deliberately**: it leaks whether a domain has an account
    # here to anyone who can type an email address. ADR-0006 decision 10
    # records the trade; `/auth/sso/{org_slug}/start` is the entry point that
    # always works without it.
    domain_hint_enabled: bool = Field(default=False)
    enabled: bool = Field(default=True)
