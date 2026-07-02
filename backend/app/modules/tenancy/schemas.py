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


class OrganizationPublic(AppBaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    logo_url: str | None
    created_at: datetime


class MembershipPublic(AppBaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    org_id: UUID
    role: MemberRole
    invited_by: UUID | None
    created_at: datetime


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
