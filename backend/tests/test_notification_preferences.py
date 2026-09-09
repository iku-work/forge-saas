"""
Verifies T-101.5's DEFAULT_PREFERENCES + channels_for: every notification
type from §"User preferences" is represented, an unset user falls back to
the defaults, and a partial customisation only changes the channel(s) the
user actually touched.

T-234 adds REQUIRED_CHANNELS on top: the dunning sequence's two types keep
their email channel no matter what is stored, and normalize() makes the
stored matrix say so rather than leaving the settings page to render a
checkbox whose state the send path ignores.
"""

import pytest

from app.modules.notifications.preferences import (
    CHANNELS,
    DEFAULT_PREFERENCES,
    REQUIRED_CHANNELS,
    channels_for,
    is_required,
    normalize,
)

_ALL_NOTIFICATION_TYPES = {
    "member_invited",
    "payment_failed",
    "subscription_expiring",
    "weekly_summary",
    "file_uploaded",
    "file_infected",
    # T-321: an outbound-webhook endpoint switched off after repeated failed
    # deliveries. Written out here rather than derived from DEFAULT_PREFERENCES
    # on purpose -- that would make the assertion below a tautology, and this
    # list is the second opinion that catches a type added to the code and
    # nowhere else (packages/types, the settings page's labels, five locales).
    "webhook_endpoint_disabled",
}


def test_default_preferences_cover_every_notification_type() -> None:
    assert set(DEFAULT_PREFERENCES) == _ALL_NOTIFICATION_TYPES
    for channel_prefs in DEFAULT_PREFERENCES.values():
        assert set(channel_prefs) == set(CHANNELS)


def test_channels_for_falls_back_to_defaults_when_user_has_no_override() -> None:
    assert channels_for({}, "payment_failed") == ["email", "in_app", "push"]


def test_channels_for_uses_user_override_when_present() -> None:
    prefs = {"member_invited": {"email": False, "in_app": True, "push": True}}

    assert channels_for(prefs, "member_invited") == ["in_app", "push"]


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


# --- required channels (T-234) -------------------------------------------------------


@pytest.mark.parametrize("notification_type", sorted(REQUIRED_CHANNELS))
def test_required_channels_name_real_types_and_channels(notification_type: str) -> None:
    assert notification_type in DEFAULT_PREFERENCES
    for channel in REQUIRED_CHANNELS[notification_type]:
        assert channel in CHANNELS


@pytest.mark.parametrize("notification_type", sorted(REQUIRED_CHANNELS))
def test_a_required_channel_survives_being_stored_as_off(notification_type: str) -> None:
    """The point of the whole mechanism: a row written before the channel
    became required -- or by anything that bypassed the update endpoint --
    still can't suppress the dunning email."""
    prefs = {notification_type: dict.fromkeys(CHANNELS, False)}

    assert channels_for(prefs, notification_type) == ["email"]


def test_a_required_channel_does_not_force_on_the_optional_ones() -> None:
    prefs = {"payment_failed": {"email": False, "in_app": False, "push": False}}

    assert channels_for(prefs, "payment_failed") == ["email"]


def test_is_required_is_false_for_ordinary_types_and_channels() -> None:
    assert not is_required("payment_failed", "push")
    assert not is_required("member_invited", "email")
    assert not is_required("not_a_real_type", "email")


def test_normalize_forces_required_channels_on() -> None:
    normalized = normalize({"payment_failed": {"email": False, "in_app": False, "push": True}})

    assert normalized == {"payment_failed": {"email": True, "in_app": False, "push": True}}


def test_normalize_leaves_everything_else_untouched() -> None:
    prefs = {
        "member_invited": {"email": False, "in_app": True, "push": False},
        "file_infected": {"email": False, "in_app": False, "push": False},
    }

    assert normalize(prefs) == prefs


def test_normalize_adds_a_required_channel_a_partial_row_left_out() -> None:
    assert normalize({"payment_failed": {"push": False}}) == {
        "payment_failed": {"push": False, "email": True}
    }


def test_normalize_does_not_invent_the_optional_channels_a_row_left_out() -> None:
    # Those stay absent so channels_for()'s per-channel default fallback keeps
    # working -- writing them here would freeze today's defaults into the row.
    assert "in_app" not in normalize({"payment_failed": {"push": False}})["payment_failed"]
