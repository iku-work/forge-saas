"""
Request/response schemas for the api_keys module (T-310).

§0.4, no mass assignment: the request schemas are hand-written lists of the
fields a caller may set, never `ApiKey` itself. Nothing here accepts `id`,
`org_id`, `prefix`, `key_hash`, `created_by`, `last_used_at` or `revoked_at`
-- a client that sends them is ignored, because there is no field to bind
them to.

`ApiKeyPublic` is the response every read produces, and the field that is
*not* on it is the point: there is no `key`, no `key_hash` and no `secret`
anywhere in it. The full credential exists in exactly one response shape,
`ApiKeyCreated`, returned once by the create endpoint.
"""

from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from app.core.schemas import AppBaseModel


class ApiKeyCreateRequest(AppBaseModel):
    name: str = Field(min_length=1, max_length=100)
    # `list[str]`, not `list[Scope]`: an unknown scope is rejected by the
    # service with a message naming the offending value, rather than by
    # pydantic with an anonymous enum dump -- see scopes.parse_scopes.
    scopes: list[str] = Field(min_length=1)
    # None means "never expires". A caller that wants an expiry sends one;
    # the template does not invent a default lifetime, because a key that
    # stops working ninety days after an integration shipped is an outage
    # nobody scheduled.
    expires_at: datetime | None = None


class ApiKeyUpdateRequest(AppBaseModel):
    """PATCH semantics: every field optional, and the service writes only the
    keys the caller actually sent (`model_dump(exclude_unset=True)`).

    Scopes are deliberately absent. Widening a live key's scopes is the same
    act as minting a new key with more access, minus the audit trail and
    minus the chance to rotate the secret -- so it is not an edit, it is a
    new key and a revocation.
    """

    name: str | None = Field(default=None, min_length=1, max_length=100)
    # T-312's per-key override. 0 is excluded rather than meaning "blocked":
    # a limit of zero is a key that cannot be used, which is what revocation
    # is for and which nobody would mean to configure by accident.
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=100_000)


class ApiKeyPublic(AppBaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    prefix: str
    scopes: list[str]
    created_by: UUID | None
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    rate_limit_per_minute: int | None


class ApiKeyCreated(AppBaseModel):
    """The one shape that carries the secret, returned by exactly one
    endpoint, exactly once. The key is not stored, so this response is the
    only opportunity anyone will ever have to read it -- which is why the
    management UI (T-313) shows it with an unmissable warning and holds it in
    component state rather than in the react-query cache."""

    model_config = ConfigDict(from_attributes=True)

    key: str
    api_key: ApiKeyPublic
