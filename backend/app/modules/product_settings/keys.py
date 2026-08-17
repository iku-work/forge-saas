"""
The well-known ProductSettings keys (T-263), and the only validation applied
to a value on the way in.

"Documented, not hard-coded" is the actual requirement, and the two halves
pull in opposite directions:

  * A key an operator can set to any JSON at all is a key that turns a typo
    (`"maintenance_mode": "false"` -- a *string*, which is truthy) into an
    outage nobody can explain. So the keys this file knows about are typed,
    and a write that doesn't match is rejected at the API boundary with a 422.
  * A key nobody may set unless a developer adds it here is not a runtime
    switch, it is a slower deploy. So an *unknown* key is accepted and stored
    verbatim -- that is what the admin UI's raw-JSON editor is for, and what
    lets a product built on this template ship its own switches without
    touching the template's code.

The registry below is therefore a validation *and documentation* table, not a
whitelist. `WELL_KNOWN` is what the admin UI renders its typed form from, and
what docs/runbooks/maintenance-mode.md points at rather than restating.

Setting a key to `None` deletes it, for every key -- which is how a switch
returns to its default rather than being pinned to a falsy value that looks
identical in the UI but reads differently in a raw JSON diff.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

BANNER_TYPES: tuple[str, ...] = ("info", "warning", "critical")

# Key names, as constants, for the same reason cache keys are: a switch read
# by an f-string at one call site is a switch nobody can find from the write
# side. Every module that consults one imports it from here.
MAINTENANCE_MODE = "maintenance_mode"
MAINTENANCE_MESSAGE = "maintenance_message"
SIGNUP_DISABLED = "signup_disabled"
GLOBAL_BANNER = "global_banner"
EMERGENCY_KILL_SWITCH = "emergency_kill_switch"

ValueKind = Literal["bool", "str", "banner", "kill_switch"]


@dataclass(frozen=True)
class WellKnownKey:
    key: str
    kind: ValueKind
    # Rendered by the admin UI beside the field, and by `GET
    # /admin/settings/product`, so the operator reading the form gets the same
    # explanation as the developer reading this file.
    description: str
    default: Any


WELL_KNOWN: tuple[WellKnownKey, ...] = (
    WellKnownKey(
        key=MAINTENANCE_MODE,
        kind="bool",
        description=(
            "Return 503 from every non-admin, non-health endpoint. Admin and "
            "/health stay reachable so you can turn this back off."
        ),
        default=False,
    ),
    WellKnownKey(
        key=MAINTENANCE_MESSAGE,
        kind="str",
        description=(
            "What users see while maintenance mode is on. Shown verbatim -- it "
            "is operator copy, not a translation key, so write it in the "
            "language most of your users read."
        ),
        default="",
    ),
    WellKnownKey(
        key=SIGNUP_DISABLED,
        kind="bool",
        description=(
            "Reject self-serve registration (POST /auth/register). Existing "
            "users keep signing in, and invitations still work -- an existing "
            "customer adding a colleague is not signup."
        ),
        default=False,
    ),
    WellKnownKey(
        key=GLOBAL_BANNER,
        kind="banner",
        description=(
            "An announcement rendered above the app shell on web and mobile. "
            'Shape: {"type": "info|warning|critical", "message": "...", '
            '"dismissible": true}. Unset it to remove the banner.'
        ),
        default=None,
    ),
    WellKnownKey(
        key=EMERGENCY_KILL_SWITCH,
        kind="kill_switch",
        description=(
            'Per-module off switch: {"billing": true} makes every '
            "/api/v1/billing route answer 503 without a redeploy. Modules "
            "without an HTTP surface of their own cannot be killed this way."
        ),
        default={},
    ),
)

WELL_KNOWN_BY_KEY: dict[str, WellKnownKey] = {entry.key: entry for entry in WELL_KNOWN}

# Module name -> the URL prefix EMERGENCY_KILL_SWITCH turns off for it.
#
# Only modules whose entire HTTP surface sits under a prefix that is theirs
# alone can appear here, which is a smaller set than "the enabled modules":
#
#   * `gdpr` mounts on /api/v1/me, shared with auth's own profile routes and
#     T-262's /me/support-context -- killing it by prefix would take a user's
#     ability to read their own account with it;
#   * `feature_flags` mounts on /api/v1/orgs/{org_id}/feature-flags, under
#     tenancy's /orgs;
#   * `oauth` and `totp_2fa` live under /api/v1/auth/*, and killing those
#     takes authentication down with them;
#   * `multi_org` has no routes of its own, and neither does T-290's
#     `marketing` -- it gates a separate site, which Caddy serves and this
#     process never sees. `public_api` and `enterprise_sso` will each qualify
#     once they have routes (T-310, T-331); `enterprise_sso`'s live under
#     /api/v1/auth/*, so it inherits the objection above rather than an entry;
#   * `admin` is absent deliberately -- a kill switch that locks the operator
#     out of the surface that undoes it is a trap, not a safety feature.
#
# A module not listed here is rejected by _validate_kill_switch rather than
# silently accepted and ignored, so nobody flips a switch that does nothing.
#
# Killing `billing` also stops POST /api/v1/billing/webhooks/stripe answering.
# That is survivable and on purpose -- Stripe retries a failed webhook for up
# to three days (T-070's handler is idempotent on event id), so the events
# arrive once the switch goes back off -- but it is the one entry here with a
# consequence outside this system, so it is written down.
KILLABLE_MODULE_PREFIXES: dict[str, str] = {
    "billing": "/api/v1/billing",
    "file_storage": "/api/v1/files",
    "notifications": "/api/v1/notifications",
}


class SettingsValidationError(ValueError):
    """A well-known key was given a value of the wrong shape. Raised by
    `validate`, translated into a 422 by the admin router -- deliberately a
    plain ValueError subclass rather than a DomainError, because this module
    is consulted from the middleware too, where nothing would catch one."""


def validate(key: str, value: Any) -> Any:  # noqa: ANN401 -- the value is arbitrary JSON by design
    """The value as it should be stored, or raise SettingsValidationError.

    An unknown key passes through untouched (see the module docstring). `None`
    is always allowed: it means "unset", and the caller deletes rather than
    stores it.
    """
    if value is None:
        return None
    entry = WELL_KNOWN_BY_KEY.get(key)
    if entry is None:
        return value
    if entry.kind == "bool":
        return _expect(key, value, bool, "a boolean")
    if entry.kind == "str":
        return _expect(key, value, str, "a string")
    if entry.kind == "banner":
        return _validate_banner(key, value)
    return _validate_kill_switch(key, value)


def _expect(key: str, value: Any, expected: type, described: str) -> Any:  # noqa: ANN401 -- see validate
    # `bool` is a subclass of `int`, so an isinstance check is safe in this
    # direction but not the other -- there is no int-typed key here, and if one
    # is ever added it needs `type(value) is int` rather than isinstance.
    if not isinstance(value, expected):
        raise SettingsValidationError(f"{key} must be {described}")
    return value


def _validate_banner(key: str, value: Any) -> dict[str, Any]:  # noqa: ANN401 -- see validate
    if not isinstance(value, dict):
        raise SettingsValidationError(f"{key} must be an object")
    banner_type = value.get("type")
    if banner_type not in BANNER_TYPES:
        raise SettingsValidationError(f"{key}.type must be one of {', '.join(BANNER_TYPES)}")
    message = value.get("message")
    if not isinstance(message, str) or not message.strip():
        raise SettingsValidationError(f"{key}.message must be a non-empty string")
    dismissible = value.get("dismissible", True)
    if not isinstance(dismissible, bool):
        raise SettingsValidationError(f"{key}.dismissible must be a boolean")
    # Rebuilt rather than passed through: an operator pasting JSON into the raw
    # editor should not be able to smuggle extra keys into a payload every
    # frontend renders.
    return {"type": banner_type, "message": message, "dismissible": dismissible}


def _validate_kill_switch(key: str, value: Any) -> dict[str, bool]:  # noqa: ANN401 -- see validate
    if not isinstance(value, dict):
        raise SettingsValidationError(f"{key} must be an object of module -> boolean")
    for module, enabled in value.items():
        if module not in KILLABLE_MODULE_PREFIXES:
            raise SettingsValidationError(
                f"{key}: {module!r} has no HTTP surface to kill "
                f"(killable: {', '.join(sorted(KILLABLE_MODULE_PREFIXES))})"
            )
        if not isinstance(enabled, bool):
            raise SettingsValidationError(f"{key}.{module} must be a boolean")
    return dict(value)


def killed_module_for_path(kill_switch: dict[str, Any], path: str) -> str | None:
    """The module whose kill switch covers `path`, if any is on.

    Prefix matching is on a path *segment* boundary so `/api/v1/billingsomething`
    -- a route that does not exist today but could -- is never swept up by
    `billing`'s switch.
    """
    for module, prefix in KILLABLE_MODULE_PREFIXES.items():
        if not kill_switch.get(module):
            continue
        if path == prefix or path.startswith(f"{prefix}/"):
            return module
    return None
