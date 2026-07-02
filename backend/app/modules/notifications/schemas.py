"""
Request/response schemas for the notifications module (T-101.5, T-103).
Distinct from the SQLModel table classes in app.modules.notifications.models
per §0.4 -- in particular the preferences matrix doesn't even live in this
module's own tables; it's app.modules.auth.models.User.notification_preferences,
so there's no ORM object to mirror, only the wire shape
`PATCH /api/v1/me/notification-preferences` accepts and returns.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, field_validator

from app.core.schemas import AppBaseModel
from app.modules.notifications.preferences import CHANNELS, DEFAULT_PREFERENCES


class NotificationPreferencesUpdate(AppBaseModel):
    preferences: dict[str, dict[str, bool]]

    @field_validator("preferences")
    @classmethod
    def _check_known_types_and_channels(
        cls, value: dict[str, dict[str, bool]]
    ) -> dict[str, dict[str, bool]]:
        for notification_type, channel_prefs in value.items():
            if notification_type not in DEFAULT_PREFERENCES:
                msg = f"Unknown notification type '{notification_type}'"
                raise ValueError(msg)
            unknown_channels = set(channel_prefs) - set(CHANNELS)
            if unknown_channels:
                msg = f"Unknown channel(s) for '{notification_type}': {sorted(unknown_channels)}"
                raise ValueError(msg)
        return value


class NotificationPublic(AppBaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    title: str
    body: str
    action_url: str | None
    channels: list[str]
    read_at: datetime | None
    created_at: datetime


class UnreadCountResponse(AppBaseModel):
    count: int


class MarkAllReadResponse(AppBaseModel):
    updated: int


class PushTokenRegisterRequest(AppBaseModel):
    token: str
    platform: Literal["ios", "android"]
    device_label: str | None = None


class PushTokenPublic(AppBaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    platform: str
    device_label: str | None
