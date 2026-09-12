"""Naming the device a session was created on.

Session.device_label, SessionPublic.device_label and the security page's
device column all existed; AuthService passed None at every call site, so the
list read "Unknown device" for every session a user had. The user agent was
stored on the adjacent line.

The strings below are real, and each is chosen for a way user agents lie. The
order of the checks in device_label.py *is* the logic, so these are the tests
that keep that order honest.
"""

import pytest

from app.modules.auth.device_label import describe_device


@pytest.mark.parametrize(
    ("user_agent", "expected"),
    [
        pytest.param(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
            "Chrome (Windows)",
            id="chrome-claims-safari",
        ),
        pytest.param(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
            "Edge (Windows)",
            id="edge-claims-chrome-and-safari",
        ),
        pytest.param(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 OPR/106.0.0.0",
            "Opera (Windows)",
            id="opera-claims-chrome-and-safari",
        ),
        pytest.param(
            "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 "
            "(KHTML, like Gecko) SamsungBrowser/23.0 Chrome/115.0.0.0 Mobile Safari/537.36",
            "Samsung Internet (Android)",
            id="samsung-claims-chrome-safari-and-linux",
        ),
        pytest.param(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
            "Safari (macOS)",
            id="real-safari",
        ),
        pytest.param(
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
            "Safari (iPhone)",
            id="iphone-says-mac-os-x-too",
        ),
        pytest.param(
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) CriOS/120.0.0.0 Mobile/15E148 Safari/604.1",
            "Chrome (iPhone)",
            id="chrome-on-ios-is-crios",
        ),
        pytest.param(
            "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
            "Firefox (Linux)",
            id="firefox-does-not-claim-webkit",
        ),
        pytest.param(
            "Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Chrome (ChromeOS)",
            id="chromeos-before-linux",
        ),
    ],
)
def test_reads_the_browser_and_platform_past_the_compatibility_lies(
    user_agent: str, expected: str
) -> None:
    assert describe_device(user_agent) == expected


@pytest.mark.parametrize(
    "user_agent",
    [
        pytest.param("curl/8.4.0", id="a-tool"),
        pytest.param("", id="empty"),
        pytest.param(None, id="absent"),
    ],
)
def test_says_nothing_rather_than_guessing(user_agent: str | None) -> None:
    """None renders as "Unknown device", which is the honest answer. The point
    of this list is spotting a session you do not recognise, so a confidently
    wrong label is worse than an admitted blank."""
    assert describe_device(user_agent) is None


def test_one_half_is_still_worth_saying() -> None:
    """ "Which of my devices is this" is often answered by the platform alone."""
    assert describe_device("Mozilla/5.0 (Windows NT 10.0; Win64; x64)") == "Windows"
