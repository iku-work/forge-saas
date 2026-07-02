"""
Verifies T-101.5's DEFAULT_PREFERENCES + channels_for: every notification
type from §"User preferences" is represented, an unset user falls back to
the defaults, and a partial customisation only changes the channel(s) the
user actually touched.
"""

from app.modules.notifications.preferences import CHANNELS, DEFAULT_PREFERENCES, channels_for

_ALL_NOTIFICATION_TYPES = {
    "member_invited",
    "payment_failed",
    "subscription_expiring",
    "weekly_summary",
    "file_uploaded",
    "file_infected",
}


def test_default_preferences_cover_every_notification_type() -> None:
    assert set(DEFAULT_PREFERENCES) == _ALL_NOTIFICATION_TYPES
    for channel_prefs in DEFAULT_PREFERENCES.values():
        assert set(channel_prefs) == set(CHANNELS)


def test_channels_for_falls_back_to_defaults_when_user_has_no_override() -> None:
    assert channels_for({}, "payment_failed") == ["email", "in_app", "push"]


def test_channels_for_uses_user_override_when_present() -> None:
    prefs = {"payment_failed": {"email": False, "in_app": True, "push": True}}

    assert channels_for(prefs, "payment_failed") == ["in_app", "push"]


def test_channels_for_does_not_leak_overrides_across_notification_types() -> None:
    prefs = {"payment_failed": {"email": False, "in_app": False, "push": False}}

    assert channels_for(prefs, "weekly_summary") == ["email"]


def test_channels_for_falls_back_per_channel_when_override_omits_a_key() -> None:
    # A row saved before a channel existed in DEFAULT_PREFERENCES shouldn't be
    # silently treated as "disabled" for that channel once it's added.
    prefs = {"file_uploaded": {"in_app": False}}

    assert channels_for(prefs, "file_uploaded") == []


def test_channels_for_unknown_notification_type_returns_no_channels() -> None:
    assert channels_for({}, "not_a_real_type") == []
