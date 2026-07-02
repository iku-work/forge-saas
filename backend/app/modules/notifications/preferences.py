"""
T-101.5: default notification preferences and the channel-resolution helper
NotificationService.send() (T-102) calls to decide where a notification goes.
User.notification_preferences (the JSONB column T-013 added) starts out as
`{}` for every account -- backfill_notification_preferences
(app.workers.jobs.notifications) is what actually writes DEFAULT_PREFERENCES
onto existing rows; this module only defines the values, not the write path.
"""

CHANNELS: tuple[str, ...] = ("email", "in_app", "push")

DEFAULT_PREFERENCES: dict[str, dict[str, bool]] = {
    "member_invited": {"email": True, "in_app": True, "push": False},
    "payment_failed": {"email": True, "in_app": True, "push": True},
    "subscription_expiring": {"email": True, "in_app": True, "push": False},
    "weekly_summary": {"email": True, "in_app": False, "push": False},
    "file_uploaded": {"email": False, "in_app": True, "push": False},
    "file_infected": {"email": True, "in_app": True, "push": True},
}


def channels_for(prefs: dict[str, dict[str, bool]], notification_type: str) -> list[str]:
    """Enabled channels for this user + this notification type.

    Falls back to DEFAULT_PREFERENCES wholesale when the user has no entry
    for `notification_type` at all. If they do have one (the settings page
    always submits the full per-type row), it's used as-is -- except a key
    missing from *that* row still falls back to its own default rather than
    being treated as disabled, so a future channel added to DEFAULT_PREFERENCES
    doesn't silently turn off for everyone who customised this type before
    that channel existed.
    """
    defaults = DEFAULT_PREFERENCES.get(notification_type, {})
    overrides = prefs.get(notification_type)
    active = defaults if overrides is None else overrides
    return [channel for channel in CHANNELS if active.get(channel, defaults.get(channel, False))]
