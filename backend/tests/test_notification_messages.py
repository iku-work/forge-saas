"""
T-234: the message catalog behind every notification
(app.modules.notifications.messages). Pure lookups against
packages/i18n/locales -- no container, no session -- so a type added to
DEFAULT_PREFERENCES without its strings fails in the fastest test in the
suite rather than at send time in production.

messages.py derives its keys from the notification type instead of listing
them in a table, which removes one place a type could be half-registered but
puts the whole burden of "the strings actually exist" here.

The end-to-end behaviour those strings feed -- per-recipient rendering across
push, email and in-app -- lives in test_notification_localisation.py.
"""

import pytest

from app.core.i18n import get_supported_locales
from app.modules.notifications import messages
from app.modules.notifications.preferences import DEFAULT_PREFERENCES

_TYPES = sorted(DEFAULT_PREFERENCES)


@pytest.mark.parametrize("notification_type", _TYPES)
def test_every_type_resolves_in_english(notification_type: str) -> None:
    title, body = messages.render(notification_type, "en", {})

    assert title.strip()
    assert body.strip()


@pytest.mark.parametrize("notification_type", _TYPES)
def test_every_type_resolves_in_every_enabled_locale(notification_type: str) -> None:
    """Not merely "doesn't raise": t() would happily fall back to English
    forever. A locale shipped as complete must have its own strings, or the
    mixed-locale guarantee is English for everyone with extra steps."""
    for locale in get_supported_locales():
        title, body = messages.render(notification_type, locale, {})
        assert title.strip()
        assert body.strip()


def test_an_unknown_type_raises_rather_than_returning_a_raw_key() -> None:
    with pytest.raises(KeyError):
        messages.render("not_a_real_notification_type", "en", {})


def test_an_unsupported_locale_falls_back_to_english() -> None:
    assert messages.render("payment_failed", "xx", {}) == messages.render(
        "payment_failed", "en", {}
    )


def test_a_param_named_after_one_of_t_s_own_arguments_is_rejected_by_name() -> None:
    with pytest.raises(ValueError, match="locale"):
        messages.render("file_uploaded", "en", {"filename": "x.pdf", "locale": "fi"})


def test_params_are_interpolated() -> None:
    _title, body = messages.render("file_uploaded", "en", {"filename": "q3-report.pdf"})

    assert "q3-report.pdf" in body
    assert "{filename}" not in body


@pytest.mark.parametrize("notification_type", _TYPES)
def test_email_template_is_the_generic_one_unless_the_type_is_listed(
    notification_type: str,
) -> None:
    template = messages.email_template_for(notification_type)

    if notification_type in messages.RICH_EMAIL_URL_PROP:
        assert template == notification_type
    else:
        assert template == messages.GENERIC_EMAIL_TEMPLATE


def test_rich_email_types_are_real_notification_types() -> None:
    assert set(messages.RICH_EMAIL_URL_PROP) <= set(DEFAULT_PREFERENCES)
