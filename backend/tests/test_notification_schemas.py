"""
Verifies T-101.5's NotificationPreferencesUpdate: only known notification
types and known channels are accepted, since this schema is the only thing
standing between a PATCH body and an arbitrary-keyed
User.notification_preferences JSONB blob.
"""

import pytest
from pydantic import ValidationError

from app.modules.notifications.schemas import NotificationPreferencesUpdate


def test_accepts_a_known_type_and_channel() -> None:
    update = NotificationPreferencesUpdate(
        preferences={"payment_failed": {"email": False, "in_app": True, "push": True}}
    )

    assert update.preferences == {"payment_failed": {"email": False, "in_app": True, "push": True}}


def test_accepts_a_partial_channel_row() -> None:
    update = NotificationPreferencesUpdate(preferences={"weekly_summary": {"email": False}})

    assert update.preferences == {"weekly_summary": {"email": False}}


def test_rejects_unknown_notification_type() -> None:
    with pytest.raises(ValidationError, match="Unknown notification type"):
        NotificationPreferencesUpdate(preferences={"not_a_real_type": {"email": True}})


def test_rejects_unknown_channel() -> None:
    with pytest.raises(ValidationError, match="Unknown channel"):
        NotificationPreferencesUpdate(preferences={"payment_failed": {"sms": True}})
