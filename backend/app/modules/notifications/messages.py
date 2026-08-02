"""
T-234: the notification message catalog -- what a notification `type` says,
in which language, and which email template renders it.

Before this module existed, every caller of NotificationService.send() passed
an English `title=`/`body=` literal it had built itself (f"You invited {email}
to join {org.name}."), so a Finnish recipient got English push, email and
in-app text from the same code path an English recipient did. §0.11 forbids
exactly that. Callers now pass only `type` plus the interpolation `params`,
and the strings are looked up per recipient, at send time, from
`notifications.types.<type>.title` / `.body` in packages/i18n/locales.

Keys are derived from the type rather than listed in a table: the type *is*
the catalog entry's name, so there is no second place a new notification type
could be half-registered. test_notification_messages.py holds the other end
of that bargain -- every type in T-101.5's DEFAULT_PREFERENCES must resolve
in `en` and in every enabled locale.
"""

from typing import Any

from app.core.i18n import t

_KEY_PREFIX = "notifications.types"

# packages/email-templates' generic template (NotificationEmail.tsx): renders
# whatever already-localised title/body it's handed, for every type with no
# purpose-built template of its own.
GENERIC_EMAIL_TEMPLATE = "notification"

# Types whose email channel has a rich, purpose-built React Email template
# *and* whose `params` are exactly that template's props (see
# app.workers.jobs.billing, the only caller that sends either): type ->
# the name of that template's call-to-action URL prop, which
# NotificationService fills per recipient from the notification's own
# action_url. Everything else gets GENERIC_EMAIL_TEMPLATE.
#
# Enumerated rather than inferred from "a template with this name exists":
# a name match alone is not evidence the props line up. `file_uploaded` and
# `member_invited` each name a template whose props are a different shape
# from the notification's, which is exactly how the email channel used to
# render those two with every interpolation undefined.
RICH_EMAIL_URL_PROP: dict[str, str] = {
    "payment_failed": "billing_url",
    "subscription_expiring": "billing_url",
}


def title_key(notification_type: str) -> str:
    return f"{_KEY_PREFIX}.{notification_type}.title"


def body_key(notification_type: str) -> str:
    return f"{_KEY_PREFIX}.{notification_type}.body"


def email_template_for(notification_type: str) -> str:
    if notification_type in RICH_EMAIL_URL_PROP:
        return notification_type
    return GENERIC_EMAIL_TEMPLATE


# app.core.i18n.t's own parameter names. Interpolation values reach it as
# **params, so a notification param called "locale" would bind to t()'s
# second parameter instead and surface as an opaque TypeError from inside
# i18n -- caught here, where the offending key can actually be named.
_RESERVED_PARAM_NAMES = frozenset({"key", "locale"})


def render(notification_type: str, locale: str, params: dict[str, Any]) -> tuple[str, str]:
    """`(title, body)` for `notification_type`, translated into `locale`.

    Raises KeyError only when the type has no catalog entry in `locale` *and*
    none in the default locale -- a missing `en` key is a developer error,
    the same contract app.core.i18n.t states. An unsupported or unknown
    `locale` is not an error: t() falls back to English, so a recipient whose
    stored User.locale is a locale this deployment no longer enables still
    gets real sentences rather than raw key strings.
    """
    reserved = sorted(_RESERVED_PARAM_NAMES & params.keys())
    if reserved:
        msg = f"notification params may not be named {reserved} -- reserved by i18n.t()"
        raise ValueError(msg)
    return (
        t(title_key(notification_type), locale, **params),
        t(body_key(notification_type), locale, **params),
    )
