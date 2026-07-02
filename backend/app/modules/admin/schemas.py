"""Request/response schemas for the admin module's feature-flag endpoints (T-122)."""

from app.core.schemas import AppBaseModel


class AdminFeatureFlagState(AppBaseModel):
    resolved: bool
    override: bool | None = None


class AdminFeatureFlagsResponse(AppBaseModel):
    flags: dict[str, AdminFeatureFlagState]


class SetFeatureFlagOverrideRequest(AppBaseModel):
    enabled: bool
