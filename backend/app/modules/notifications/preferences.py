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
    # T-321: an endpoint auto-disabled after repeated failures. Email on by
    # default because the people who need to know are the org's owners, who
    # are not necessarily the ones looking at the app -- an integration that
    # stopped is exactly the thing a product finds out about a week late.
    # Push off: it is an operations message, not something to wake anyone for.
    "webhook_endpoint_disabled": {"email": True, "in_app": True, "push": False},
}

# T-234: (notification type -> channels that stay on no matter what the user
# stores). These two carry the dunning sequence (app.workers.jobs.billing's
# day-1/day-3 steps) -- "we couldn't charge your card, you are about to lose
# access". Losing that silently because a checkbox got unticked months
# earlier is a worse outcome than any notification-fatigue it prevents, and
# it's a service message rather than something the product chose to send.
#
# In-app and push for the same types stay freely toggleable, and the
# settings page renders these particular boxes checked-and-disabled (see
# packages/types' REQUIRED_CHANNELS) rather than pretending they're live.
REQUIRED_CHANNELS: dict[str, tuple[str, ...]] = {
    "payment_failed": ("email",),
    "subscription_expiring": ("email",),
}


def is_required(notification_type: str, channel: str) -> bool:
    return channel in REQUIRED_CHANNELS.get(notification_type, ())


def channels_for(prefs: dict[str, dict[str, bool]], notification_type: str) -> list[str]:
    """Enabled channels for this user + this notification type.

    Falls back to DEFAULT_PREFERENCES wholesale when the user has no entry
    for `notification_type` at all. If they do have one (the settings page
    always submits the full per-type row), it's used as-is -- except a key
    missing from *that* row still falls back to its own default rather than
    being treated as disabled, so a future channel added to DEFAULT_PREFERENCES
    doesn't silently turn off for everyone who customised this type before
    that channel existed.

    A REQUIRED_CHANNELS entry overrides all of the above. normalize() keeps
    what's *stored* in step with that, but this is the authoritative check --
    a row written before a channel became required, or by anything that
    bypassed the update endpoint, must not be able to suppress it.
    """
    defaults = DEFAULT_PREFERENCES.get(notification_type, {})
    overrides = prefs.get(notification_type)
    active = defaults if overrides is None else overrides
    return [
        channel
        for channel in CHANNELS
        if is_required(notification_type, channel)
        or active.get(channel, defaults.get(channel, False))
    ]


def normalize(prefs: dict[str, dict[str, bool]]) -> dict[str, dict[str, bool]]:
    """Forces every REQUIRED_CHANNELS entry on before the matrix is stored.

    Without this, unticking a required box would persist as `False`, the
    settings page would render it unticked on the next load, and only the
    email that arrived anyway would give the lie away. Coercing rather than
    rejecting keeps a client that predates the requirement working.

    A required channel is written even into a row that omitted it -- unlike
    the *optional* channels, which are deliberately left absent so
    channels_for()'s per-channel default fallback keeps working. Recording a
    hard requirement isn't the same as freezing today's defaults into the row.
    """
    normalized: dict[str, dict[str, bool]] = {}
    for notification_type, channel_prefs in prefs.items():
        row = dict(channel_prefs)
        for channel in REQUIRED_CHANNELS.get(notification_type, ()):
            row[channel] = True
        normalized[notification_type] = row
    return normalized
