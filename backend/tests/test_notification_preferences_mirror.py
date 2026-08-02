"""
packages/types/src/index.ts hand-copies app.modules.notifications.preferences
so the settings page can render its checkbox matrix without an endpoint to
fetch it from ("keep both in sync", says the comment above it). Nothing
enforced that, and it drifted: `file_infected` had been sent by the virus
scanner since T-102 and was missing from the TypeScript list the whole time,
so the page never offered a row for it and the one notification type most
worth muting had no controls at all.

A static text check on the TypeScript source, in the same spirit as
test_i18n_docker_context.py and test_caddy_config.py -- no Node, no build
step, just the two declarations read side by side. It fails on the next
drift in either direction rather than waiting for someone to notice a
missing row.
"""

import json
import re
from pathlib import Path

import pytest

from app.modules.notifications.preferences import (
    CHANNELS,
    DEFAULT_PREFERENCES,
    REQUIRED_CHANNELS,
)

_TYPES_SOURCE = Path(__file__).parent.parent.parent / "packages" / "types" / "src" / "index.ts"


@pytest.fixture(scope="module")
def source() -> str:
    return _TYPES_SOURCE.read_text(encoding="utf-8")


def _string_array(source: str, name: str) -> list[str]:
    """The string literals in `export const <name> = [...] as const;`."""
    match = re.search(rf"export const {name} = \[(.*?)\] as const;", source, re.DOTALL)
    assert match is not None, f"{name} not found in {_TYPES_SOURCE}"
    return re.findall(r'"([^"]+)"', match.group(1))


def test_notification_channels_match(source: str) -> None:
    assert _string_array(source, "NOTIFICATION_CHANNELS") == list(CHANNELS)


def test_notification_types_match(source: str) -> None:
    assert set(_string_array(source, "NOTIFICATION_TYPES")) == set(DEFAULT_PREFERENCES)


def test_default_preferences_match(source: str) -> None:
    match = re.search(
        r"export const DEFAULT_NOTIFICATION_PREFERENCES:.*?= \{(.*?)\n\};",
        source,
        re.DOTALL,
    )
    assert match is not None

    parsed: dict[str, dict[str, bool]] = {}
    for notification_type, row in re.findall(r"(\w+): \{([^}]*)\}", match.group(1)):
        parsed[notification_type] = {
            channel: json.loads(value) for channel, value in re.findall(r"(\w+): (true|false)", row)
        }
    assert parsed == DEFAULT_PREFERENCES


def test_required_channels_match(source: str) -> None:
    match = re.search(
        r"export const REQUIRED_NOTIFICATION_CHANNELS:.*?= \{(.*?)\n\};", source, re.DOTALL
    )
    assert match is not None

    parsed = {
        notification_type: tuple(re.findall(r'"([^"]+)"', channels))
        for notification_type, channels in re.findall(r"(\w+): \[([^\]]*)\]", match.group(1))
    }
    assert parsed == REQUIRED_CHANNELS
