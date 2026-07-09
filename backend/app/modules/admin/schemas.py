"""Request/response schemas for the admin module: feature-flag overrides
(T-122) plus the T-141 org/user directory and plan-override endpoints."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.core.schemas import AppBaseModel
from app.modules.auth.permissions import MemberRole
from app.modules.auth.schemas import UserPublic


class AdminFeatureFlagState(AppBaseModel):
    resolved: bool
    override: bool | None = None


class AdminFeatureFlagsResponse(AppBaseModel):
    flags: dict[str, AdminFeatureFlagState]


class SetFeatureFlagOverrideRequest(AppBaseModel):
    enabled: bool


# ---- T-141: org/user directory ---------------------------------------------


class AdminOrgSummary(AppBaseModel):
    id: UUID
    name: str
    slug: str
    plan_slug: str | None
    subscription_status: str | None
    created_at: datetime


class AdminSubscriptionDetail(AppBaseModel):
    plan_slug: str
    status: str
    billing_interval: str | None
    free_override: bool
    trial_end: datetime | None
    current_period_end: datetime
    override_expires_at: datetime | None


class AdminOrgDetail(AppBaseModel):
    id: UUID
    name: str
    slug: str
    created_at: datetime
    member_count: int
    file_count: int | None
    last_activity_at: datetime | None
    subscription: AdminSubscriptionDetail | None


class AdminUserSummary(AppBaseModel):
    id: UUID
    email: str
    full_name: str | None
    is_active: bool
    created_at: datetime


class AdminMembershipSummary(AppBaseModel):
    org_id: UUID
    org_name: str
    org_slug: str
    role: MemberRole


class AdminSessionSummary(AppBaseModel):
    id: UUID
    device_label: str | None
    ip_address: str
    last_seen_at: datetime
    expires_at: datetime


class AdminAuditEntry(AppBaseModel):
    action: str
    resource_type: str
    resource_id: str | None
    ts: datetime


class AdminAuditSummary(AppBaseModel):
    total_count: int
    recent_actions: list[AdminAuditEntry]


class AdminUserDetail(AppBaseModel):
    id: UUID
    email: str
    full_name: str | None
    is_active: bool
    is_superadmin: bool
    created_at: datetime
    memberships: list[AdminMembershipSummary]
    sessions: list[AdminSessionSummary]
    audit_summary: AdminAuditSummary


# ---- T-141: plan override / trial / comp-credit -----------------------------


class AdminSetOrgPlanRequest(AppBaseModel):
    plan_slug: str
    billing_interval: Literal["monthly", "yearly"] | None = None
    free_override: bool = False
    expires_at: datetime | None = None


class AdminExtendTrialRequest(AppBaseModel):
    days: int = Field(gt=0, le=365)


class AdminCompCreditRequest(AppBaseModel):
    amount_cents: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=1000)


# ---- T-142: user impersonation ----------------------------------------------


class AdminImpersonateRequest(AppBaseModel):
    reason: str = Field(min_length=10, max_length=1000)
    duration_minutes: int = Field(gt=0, le=60)


class AdminImpersonateResponse(AppBaseModel):
    access_token: str
    expires_at: datetime
    user: UserPublic


# ---- T-218: admin-assisted lost-2FA recovery ---------------------------------


class AdminDisableTotpRequest(AppBaseModel):
    reason: str = Field(min_length=10, max_length=1000)


# ---- T-143: dashboard + global audit feed -----------------------------------


class AdminPaymentFailureEntry(AppBaseModel):
    org_id: UUID
    org_name: str
    subscription_id: str | None
    ts: datetime


class AdminDashboardStats(AppBaseModel):
    org_count: int
    user_count: int
    mrr_cents: int | None  # null when the billing module is disabled
    recent_signups: list[AdminUserSummary]
    recent_payment_failures: list[AdminPaymentFailureEntry]


class AdminAuditFeedEntry(AppBaseModel):
    id: UUID
    ts: datetime
    action: str
    resource_type: str
    resource_id: str | None
    severity: str
    actor_id: UUID | None
    actor_email: str | None
    org_id: UUID | None
    org_name: str | None
    impersonator_id: UUID | None
