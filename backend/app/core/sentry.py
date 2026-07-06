"""
Sentry wiring (T-201) -- called from the app factory *before* middleware
registration so every middleware's exceptions are already reportable.

`sentry_sdk.init()` with `dsn=None` is itself a no-op (every SDK call becomes
a cheap no-op too), so this module doesn't need its own dev/prod branch --
callers just always call `init_sentry(settings)`.
"""

from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import MutableMapping

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.types import Breadcrumb, BreadcrumbHint, Event, Hint

from app.core.config import Settings
from app.core.logging import _scrub  # reuse T-011's scrubber key list, not a second copy of it

# app.main's global Exception handler (app.core.handlers) already calls
# sentry_sdk.capture_exception itself -- Starlette/FastApi's own integration
# additionally wraps every registered exception handler and would re-report
# the same event a second time for any handled exception whose class carries
# a `status_code` in this range (see DomainError subclasses in
# app/core/exceptions.py). Disabling it here means Sentry only ever hears
# about an error once, from the handler that actually decided it was
# reportable.
_DISABLE_AUTO_CAPTURE: dict[str, Any] = {"failed_request_status_codes": set()}


def _scrub_mapping(value: object) -> object:
    return _scrub(value) if isinstance(value, dict) else value


def _before_send(
    event: Event,
    hint: Hint,  # noqa: ARG001 -- sentry_sdk's before_send calling convention
) -> Event:
    if "extra" in event:
        event["extra"] = cast("MutableMapping[str, object]", _scrub_mapping(event["extra"]))
    if "contexts" in event:
        event["contexts"] = cast("dict[str, dict[str, object]]", _scrub_mapping(event["contexts"]))
    request = event.get("request")
    if isinstance(request, dict):
        for key in ("data", "headers", "cookies"):
            if key in request:
                request[key] = _scrub_mapping(request[key])
    breadcrumbs = event.get("breadcrumbs")
    if isinstance(breadcrumbs, dict):
        for crumb in breadcrumbs.get("values", []):
            if "data" in crumb:
                crumb["data"] = _scrub_mapping(crumb["data"])
    return event


def _before_breadcrumb(
    crumb: Breadcrumb,
    hint: BreadcrumbHint,  # noqa: ARG001 -- sentry_sdk's before_breadcrumb calling convention
) -> Breadcrumb:
    if "data" in crumb:
        crumb["data"] = _scrub_mapping(crumb["data"])
    return crumb


def _app_release() -> str:
    try:
        return version("forge-saas-backend")
    except PackageNotFoundError:
        return "dev"


def init_sentry(settings: Settings) -> None:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        release=_app_release(),
        send_default_pii=False,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        before_send=_before_send,
        before_breadcrumb=_before_breadcrumb,
        integrations=[
            StarletteIntegration(**_DISABLE_AUTO_CAPTURE),
            FastApiIntegration(**_DISABLE_AUTO_CAPTURE),
        ],
    )
