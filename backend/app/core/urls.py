"""T-233: builders for links that point *into* the web app.

Every web URL now carries the locale as its first path segment
(`/en/verify-email`, `/fi/accept-invitation/<token>`) -- see
apps/web/src/routes/$locale.tsx. Transactional emails are the largest source
of inbound deep links, so they have to build that segment too: a Finnish user
whose reset link lands on `/reset-password` gets bounced through the SPA's
backwards-compatibility redirect and, until that redirect resolves, has no
locale in the URL at all.

The locale is picked per recipient, exactly like the email body itself
(§0.11) -- never from the sender, never from `request.state.locale`.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.core.i18n import get_default_locale, get_supported_locales


def frontend_locale(locale: str | None) -> str:
    """The locale segment to use for `locale`, falling back to the product's
    default when it is missing or no longer enabled (a stored `User.locale`
    can name a locale the product has since turned off -- linking someone to
    `/xx/...` would 404 them out of their own password reset)."""
    if locale and locale in get_supported_locales():
        return locale
    return get_default_locale()


def frontend_url(path: str, locale: str | None = None) -> str:
    """`https://app.example.com/<locale><path>` for a `path` written without
    its locale segment, e.g. `frontend_url("/verify-email?token=x", "fi")`."""
    if not path.startswith("/"):
        raise ValueError(f"frontend_url() path must start with '/': {path!r}")
    return f"{get_settings().FRONTEND_URL}/{frontend_locale(locale)}{path}"
