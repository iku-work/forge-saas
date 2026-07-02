"""Request/response schemas for the feature_flags module (T-120)."""

from app.core.schemas import AppBaseModel


class FeatureFlagsResponse(AppBaseModel):
    flags: dict[str, bool]
