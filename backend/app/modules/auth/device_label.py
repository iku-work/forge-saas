"""A human-recognisable name for the device a session was created on.

Session.device_label existed, SessionPublic exposed it and the security page
rendered it -- but AuthService passed `device_label=None` at every call, so
every session in the list read "Unknown device". The user agent was being
stored on the very next line.

The label only has to answer one question: *which of my devices is this?*
Browser and platform are enough for that, and they are the part of a user
agent string that can be read with any confidence. Anything finer -- version
numbers, engine names, precise OS releases -- is both unreliable and useless
for the question being asked.

**This is approximate on purpose, and returns None rather than guessing.**
User agent strings are famously adversarial: every browser claims to be
several others for compatibility, so the order of the checks below *is* the
logic (Edge says "Chrome", Chrome says "Safari", nearly everything says
"Mozilla"). An unrecognised agent gives None, which the UI already renders as
"Unknown device" -- a wrong label is worse than an honest absence, because
the whole point of this list is spotting a session you do not recognise.

A dependency (`ua-parser`, and its regex database) would classify more agents
correctly. It is not obviously worth a supply-chain dependency and periodic
database updates for a string next to an IP address; if a product needs
better fidelity, replacing `describe_device` is a contained change.
"""

from __future__ import annotations

# Order matters, most specific first: Edge and Opera both contain "Chrome",
# Chrome contains "Safari", and Samsung Internet contains both.
_BROWSERS: tuple[tuple[str, str], ...] = (
    ("Edg/", "Edge"),
    ("Edge/", "Edge"),
    ("EdgiOS/", "Edge"),
    ("OPR/", "Opera"),
    ("Opera", "Opera"),
    ("SamsungBrowser", "Samsung Internet"),
    ("YaBrowser", "Yandex Browser"),
    ("Vivaldi", "Vivaldi"),
    ("Brave", "Brave"),
    ("Firefox/", "Firefox"),
    ("FxiOS/", "Firefox"),
    ("CriOS/", "Chrome"),
    ("Chromium", "Chromium"),
    ("Chrome/", "Chrome"),
    # Last: every one of the above also ships "Safari" in its agent string.
    ("Safari/", "Safari"),
)

# Again most specific first: an iPad's agent contains "Macintosh" in desktop
# mode, and Android's contains "Linux".
_PLATFORMS: tuple[tuple[str, str], ...] = (
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Android", "Android"),
    ("Windows NT", "Windows"),
    ("Macintosh", "macOS"),
    ("Mac OS X", "macOS"),
    ("CrOS", "ChromeOS"),
    ("Linux", "Linux"),
)


def _first_match(user_agent: str, table: tuple[tuple[str, str], ...]) -> str | None:
    return next((name for token, name in table if token in user_agent), None)


def describe_device(user_agent: str | None) -> str | None:
    """ "Chrome (Windows)", "Safari (iPhone)", or None when unrecognised.

    Parenthesised rather than "Chrome on Windows" because this string is
    stored once and rendered in every locale: product names do not translate,
    but the word "on" would, and there is no sensible place to translate a
    value the database hands over pre-composed. The bracket carries the same
    meaning in all six locales this ships with.
    """
    if not user_agent:
        return None

    browser = _first_match(user_agent, _BROWSERS)
    platform = _first_match(user_agent, _PLATFORMS)

    if browser and platform:
        return f"{browser} ({platform})"
    # One half is still useful -- "which of my devices" is often answered by
    # the platform alone.
    return browser or platform
