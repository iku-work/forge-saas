"""
T-263: the switch that takes the product down on purpose.

With `maintenance_mode=true` in ProductSettings, every request answers 503 --
except the ones an operator needs in order to turn it back off, and the ones a
load balancer needs in order to keep the process alive. That exception list is
the whole design; get it wrong in either direction and you have either an
outage nobody can end or a maintenance mode that doesn't maintain anything.

**Why a middleware and not a dependency.** A dependency has to be added to
every route, which means a route added next month is one nobody remembers to
gate. This runs ahead of the router, so a route that does not exist yet is
covered the day it is written.

**Where it sits in the stack.** Registered *first* in app.main, which makes it
the innermost middleware -- it runs last on the way in, immediately before the
router. That ordering is deliberate:

  * `LocaleMiddleware` and `RequestIDMiddleware` have already run, so the 503
    carries a request id and can be phrased in the caller's own language;
  * `SecurityHeadersMiddleware` and CORS still wrap it, so the 503 is a
    response a browser will actually let the app read -- a maintenance
    response that fails CORS shows the user a network error instead of the
    maintenance copy.

It also means the DomainError machinery in app.core.handlers is *inside* this
middleware and cannot catch anything raised here (Starlette installs exception
handlers between the user middleware stack and the router), which is why the
503 below is built as a JSONResponse by hand rather than raised.

**Cost.** One Redis GET per request when nothing is exempt -- the settings row
is cached with a 60s TTL by app.modules.product_settings.service. If Redis is
unreachable, that read degrades to one indexed primary-key lookup per request
instead; see docs/runbooks/maintenance-mode.md.

The three decisions this file makes that a reader is most likely to disagree
with -- the innermost registration, the exemption list, and failing *open* when
the settings cannot be read at all -- are argued in
docs/adr/0005-runtime-operator-switches.md, including why fail-closed loses.
"""

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.envelope import Envelope, ErrorBody, Meta
from app.core.i18n import get_default_locale, t
from app.modules.product_settings import keys
from app.modules.product_settings.service import (
    is_maintenance_mode,
    kill_switch,
    maintenance_message,
    read_settings,
)

# Paths that answer normally even with maintenance mode on.
#
#   /health*  -- the orchestrator's liveness and readiness probes. A
#               maintenance mode that fails these gets the container killed
#               and restarted in a loop, which is not maintenance, it is a
#               crash loop with extra steps.
#   /metrics  -- T-202's Prometheus scrape. Maintenance is exactly when you
#               most want the graphs to keep moving.
#   /api/v1/admin/*
#             -- the surface that turns this back off. Still behind
#               require_superadmin, so exempting it grants nobody anything.
#   /api/v1/config
#             -- how web and mobile learn that maintenance is on and what
#               copy to show. Blocking it means every client renders a
#               generic network error instead of the operator's message.
#   /api/v1/auth/login, /refresh, /2fa/*
#             -- a superadmin who is not already signed in cannot reach the
#               admin API without these, and "turn maintenance off" must not
#               depend on having happened to be logged in beforehand. Ordinary
#               users can authenticate too, and then get a 503 from everything
#               else, which is the honest outcome: the frontend has a session
#               to render the maintenance screen with.
#   /openapi.json, /docs, /redoc
#             -- the schema and the interactive docs, which only exist at all
#               when ENVIRONMENT=dev (app.main gates them). They are not
#               product traffic, and they are reached by tooling that does not
#               start the app's lifespan (T-050's schemathesis discovery call
#               being the one in this repo), so they must not depend on
#               anything lifespan sets up.
_EXEMPT_PREFIXES: tuple[str, ...] = (
    "/health",
    "/metrics",
    "/openapi.json",
    "/docs",
    "/redoc",
    "/api/v1/admin",
    "/api/v1/config",
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
    "/api/v1/auth/logout",
    "/api/v1/auth/2fa",
)

MAINTENANCE_CODE = "maintenance"
MAINTENANCE_MESSAGE_KEY = "errors.maintenance"
KILL_SWITCH_CODE = "module_disabled"
KILL_SWITCH_MESSAGE_KEY = "errors.module_disabled"

# Advisory only -- a client is free to ignore it -- but it is the difference
# between a mobile app that retries in a minute and one that retries in a
# tight loop for the duration of the window.
_RETRY_AFTER_SECONDS = "60"


def _is_exempt(path: str) -> bool:
    # Segment-boundary match, not a bare startswith: a future
    # `/api/v1/configuration` route must not inherit `/api/v1/config`'s
    # exemption. `/health` and `/metrics` match exactly too, plus their
    # children (`/health/ready`).
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in _EXEMPT_PREFIXES)


class MaintenanceModeMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if _is_exempt(path):
            return await call_next(request)

        # CORS preflight carries no credentials and mutates nothing, and
        # blocking it means the browser never even sends the real request --
        # so the client sees a CORS failure rather than the 503 this
        # middleware wants it to see and render.
        if request.method == "OPTIONS":
            return await call_next(request)

        settings = await self._settings(request)

        if is_maintenance_mode(settings):
            return self._blocked(
                request,
                code=MAINTENANCE_CODE,
                message_key=MAINTENANCE_MESSAGE_KEY,
                message=maintenance_message(
                    settings,
                    fallback=self._translate(
                        request,
                        MAINTENANCE_MESSAGE_KEY,
                        "The service is temporarily down for maintenance. Please try again soon.",
                    ),
                ),
            )

        killed = keys.killed_module_for_path(kill_switch(settings), path)
        if killed is not None:
            return self._blocked(
                request,
                code=KILL_SWITCH_CODE,
                message_key=KILL_SWITCH_MESSAGE_KEY,
                message=self._translate(
                    request,
                    KILL_SWITCH_MESSAGE_KEY,
                    "This feature is temporarily unavailable. Please try again soon.",
                    feature=killed,
                ),
                details={"module": killed},
            )

        return await call_next(request)

    async def _settings(self, request: Request) -> dict[str, Any]:
        """The settings dict, or `{}` if it cannot be read at all.

        Fail *open*, deliberately, and this is the one judgement call in this
        file worth arguing with. A switch whose backing store is unreachable
        must not itself become the outage: answering 503 to every request
        because Redis and Postgres are both unreachable turns a degraded
        system into a fully dead one, and it does so at exactly the moment an
        operator is trying to diagnose it. The failure is loud in the logs
        instead.

        `getattr` rather than `request.app.state.redis`: Starlette's State
        raises AttributeError for an unset key, and the attribute is set by
        the lifespan -- which tooling that mounts the ASGI app directly (a
        schema-discovery call, say) never runs.
        """
        redis = getattr(request.app.state, "redis", None)
        if redis is None:
            return {}
        try:
            return await read_settings(redis)
        except Exception as exc:  # noqa: BLE001 -- see the docstring: this must degrade, not 503
            logger.warning("maintenance.settings_unreadable: error={}", exc)
            return {}

    def _translate(self, request: Request, key: str, fallback: str, **params: str) -> str:
        locale: str | None = getattr(request.state, "locale", None)
        try:
            return t(key, locale or get_default_locale(), **params)
        except KeyError:
            # Same posture as app.core.handlers._translate_or_fallback: a
            # missing catalog entry must never turn a 503 into a 500.
            return fallback

    def _blocked(
        self,
        request: Request,
        *,
        code: str,
        message_key: str,
        message: str,
        details: dict[str, str] | None = None,
    ) -> JSONResponse:
        request_id: str | None = getattr(request.state, "request_id", None)
        envelope = Envelope[None](
            error=ErrorBody(code=code, message_key=message_key, message=message, details=details),
            meta=Meta(request_id=request_id or str(uuid4())),
        )
        return JSONResponse(
            status_code=503,
            content=envelope.model_dump(mode="json"),
            headers={"Retry-After": _RETRY_AFTER_SECONDS},
        )
