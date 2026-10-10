"""
Request/response schemas for the tenancy module (T-027). Always distinct
from the SQLModel table classes in app.modules.tenancy.models -- a router
must never accept or return a model directly (§0.4's mass-assignment rule).
"""

from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, EmailStr, Field

from app.core.schemas import AppBaseModel
from app.modules.auth.permissions import MemberRole


class CreateOrgRequest(AppBaseModel):
    name: str = Field(min_length=1, max_length=255)


class UpdateOrgRequest(AppBaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    logo_url: str | None = None
    # The id of a file uploaded to this org's "logos" bucket; null clears the
    # logo. The server resolves it to a storage key -- a client-supplied key
    # would be a request to presign any object in the bucket.
    logo_file_id: UUID | None = None


class OrganizationPublic(AppBaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    logo_url: str | None
    # Whether an uploaded logo exists; GET /orgs/{id}/logo serves it.
    has_uploaded_logo: bool = False
    created_at: datetime


class MemberUserSummary(AppBaseModel):
    """Who a membership belongs to, as fellow members see them.

    Enough to draw a row (a name, an email, a picture) and no more. Never the
    uploaded avatar's object key: `has_uploaded_avatar` only says one exists
    (the web app fetches a short-lived URL for it with a member's session).
    `avatar_url` is an OAuth provider's picture, already an absolute URL.
    """

    full_name: str | None
    email: str
    avatar_url: str | None
    has_uploaded_avatar: bool


class MembershipPublic(AppBaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    org_id: UUID
    role: MemberRole
    invited_by: UUID | None
    created_at: datetime
    # Null only when the user row is gone (soft-deleted) and the membership
    # is not yet: the GDPR purge removes both, but not in one statement.
    user: MemberUserSummary | None = None


class UpdateMembershipRequest(AppBaseModel):
    role: MemberRole


class InviteRequest(AppBaseModel):
    email: EmailStr
    role: MemberRole


class InvitationPreview(AppBaseModel):
    org_id: UUID
    org_name: str
    email: str
    role: MemberRole
    expires_at: datetime


class InvitationPublic(AppBaseModel):
    """One pending invitation, as the org's own admins see it.

    Field-by-field rather than from_attributes over the whole Invitation row
    on purpose: that row carries `token_hash`, and the accept URL's security
    rests on the token being unguessable. An explicit allowlist means a
    future column added to the model can't ride out to the client the way
    `model_config = ConfigDict(from_attributes=True)` plus a wildcard would
    (§0.4). `expires_at` is here so the caller can render an unaccepted-but-
    expired invite as expired rather than as still-live."""

    id: UUID
    org_id: UUID
    email: str
    role: MemberRole
    invited_by: UUID
    expires_at: datetime
    created_at: datetime
