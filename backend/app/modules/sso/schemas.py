"""
Request/response schemas for the `enterprise_sso` module (T-331).

§0.4, no mass assignment: the request schemas are hand-written lists of the
fields a caller may set, never `SsoConnection` itself.

`SsoConnectionPublic` has **no `client_secret`**, and unlike T-310's API key
or T-320's webhook secret there is no "shown once" response either — an IdP
client secret is a value the admin already holds (their IdP generated it),
so this product never needs to hand it back. Losing it is a trip to the IdP,
not to us.
"""

from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator

from app.core.schemas import AppBaseModel
from app.modules.auth.permissions import MemberRole


def _normalise_domain(value: str) -> str:
    """`@Acme.COM ` -> `acme.com`.

    Accepting the leading `@` is not politeness: an admin copying a domain
    out of their IdP console types it about half the time, and a connection
    whose `email_domain` is `@acme.com` matches nothing and fails at login
    rather than at save.
    """
    return value.strip().lstrip("@").lower()


class SsoConnectionUpsertRequest(AppBaseModel):
    provider_label: str = Field(min_length=1, max_length=100)
    issuer: str = Field(min_length=1, max_length=500)
    discovery_url: str = Field(min_length=1, max_length=500)
    client_id: str = Field(min_length=1, max_length=255)
    client_secret: str = Field(min_length=1)
    email_domain: str = Field(min_length=1, max_length=255)
    jit_provisioning: bool = True
    default_role: MemberRole = MemberRole.MEMBER
    group_claim: str | None = Field(default=None, max_length=100)
    group_role_mappings: dict[str, MemberRole] = Field(default_factory=dict)
    enforced: bool = False
    domain_hint_enabled: bool = False
    enabled: bool = True

    @field_validator("email_domain")
    @classmethod
    def _clean_domain(cls, value: str) -> str:
        return _normalise_domain(value)

    @field_validator("default_role")
    @classmethod
    def _refuse_owner_by_default(cls, value: MemberRole) -> MemberRole:
        """An IdP tells this product *who* somebody is and nothing about what
        they may do here. `owner` as a JIT default would let anyone the IdP
        authenticates take over billing and membership, which is not a
        setting anybody means to choose."""
        if value is MemberRole.OWNER:
            raise ValueError("default_role cannot be owner")
        return value


class SsoConnectionPublic(AppBaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID
    provider_label: str
    issuer: str
    discovery_url: str
    client_id: str
    email_domain: str
    jit_provisioning: bool
    default_role: MemberRole
    group_claim: str | None
    group_role_mappings: dict[str, str]
    enforced: bool
    domain_hint_enabled: bool
    enabled: bool
    created_at: datetime
    updated_at: datetime


class SsoHintRequest(AppBaseModel):
    email: str = Field(min_length=3, max_length=320)


class SsoHintResponse(AppBaseModel):
    """What the login page needs to replace a password field with a button.

    `sso_available: false` is the answer for every domain with no *opt-in*
    connection -- including domains that have one with hinting turned off.
    The two are deliberately indistinguishable from outside: that is the
    whole point of the flag.
    """

    sso_available: bool
    org_slug: str | None = None
    provider_label: str | None = None
