"""
T-063: a single unauthenticated endpoint that tells every frontend surface
(web, mobile) exactly which modules are active and how the product is
branded. Frontend routes, nav items, and UI sections read this response
instead of hardcoding flags -- see packages/hooks/src/useModules.ts.
"""

from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from redis.asyncio import Redis

from app.core.dependencies import get_redis
from app.core.envelope import Envelope, Meta
from app.core.i18n import get_default_locale, get_supported_locales
from app.core.module_loader import get_branding, get_modules, get_support_config
from app.core.support import SupportTool
from app.middleware.rate_limit import api_limit
from app.modules.product_settings import service as product_settings

router = APIRouter(tags=["config"])

# T-263 lowered this from 300s. The response is no longer purely deploy-time:
# it now carries the global banner and the maintenance flag, which an operator
# changes during an incident and expects to see propagate. A write busts the
# server-side Redis copy immediately, but nothing can reach into a browser's
# HTTP cache -- so this max-age *is* the worst-case delay a user sees, and it
# is pinned to the same 60s as the settings cache behind it rather than left
# five times longer.
_CACHE_CONTROL = f"public, max-age={product_settings.TTL_SECONDS}"


class ModulesResponse(BaseModel):
    billing: bool
    notifications: bool
    file_storage: bool
    feature_flags: bool
    gdpr: bool
    admin: bool
    oauth: bool
    totp_2fa: bool
    multi_org: bool
    usage_metering: bool
    outbound_webhooks: bool


class GlobalBannerResponse(BaseModel):
    """T-263's `global_banner` setting, rendered above the app shell on web
    and mobile. Absent (null) whenever no banner is set."""

    type: Literal["info", "warning", "critical"]
    message: str
    dismissible: bool


class MaintenanceResponse(BaseModel):
    """T-263. Reported here -- on the one endpoint the maintenance middleware
    deliberately never blocks -- so a client that is being 503'd everywhere
    else can still learn *why*, and render the operator's copy instead of a
    generic network error."""

    enabled: bool
    message: str


class ConfigResponse(BaseModel):
    app_name: str
    primary_color: str
    logo_url: str
    support_email: str
    modules: ModulesResponse
    supported_locales: list[str]
    default_locale: str
    # T-262: deploy-time, product-wide and identical for every user, so it
    # rides along on this already-cached response rather than on the
    # per-user /me/support-context one. Never the user's own context --
    # that endpoint is authenticated for a reason, this one is public.
    support_tool: SupportTool
    support_url: str
    # T-263: the runtime half of this response. Everything above is
    # deploy-time and changes only with a redeploy; these three change when an
    # operator flips a switch in the admin UI, which is why the Cache-Control
    # above came down to match the settings TTL.
    banner: GlobalBannerResponse | None = None
    maintenance: MaintenanceResponse
    signup_disabled: bool = False


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid4())


@router.get("/config", include_in_schema=True)
@api_limit
async def get_config(
    request: Request,
    response: Response,
    redis: Annotated[Redis, Depends(get_redis)],
) -> Envelope[ConfigResponse]:
    m = get_modules()
    b = get_branding()
    s = get_support_config()
    # Read through the same 60s Redis cache the maintenance middleware uses,
    # so this endpoint costs one GET rather than a DB round trip on what is
    # the single most-requested unauthenticated route in the product.
    runtime = await product_settings.read_settings(redis)
    banner = product_settings.global_banner(runtime)
    response.headers["Cache-Control"] = _CACHE_CONTROL
    data = ConfigResponse(
        app_name=b.app_name,
        primary_color=b.primary_color,
        logo_url=b.logo_url,
        support_email=b.support_email,
        modules=ModulesResponse(
            billing=m.billing.enabled,
            notifications=m.notifications.enabled,
            file_storage=m.file_storage.enabled,
            feature_flags=m.feature_flags.enabled,
            gdpr=m.gdpr.enabled,
            admin=m.admin.enabled,
            oauth=m.oauth.enabled,
            totp_2fa=m.totp_2fa.enabled,
            multi_org=m.multi_org.enabled,
            usage_metering=m.usage_metering.enabled,
            outbound_webhooks=m.outbound_webhooks.enabled,
        ),
        supported_locales=get_supported_locales(),
        default_locale=get_default_locale(),
        support_tool=s.tool,
        support_url=s.url,
        banner=GlobalBannerResponse(**banner) if banner is not None else None,
        maintenance=MaintenanceResponse(
            enabled=product_settings.is_maintenance_mode(runtime),
            # Empty string when maintenance is off *and* no copy was written:
            # there is nothing to translate here, because the stored message
            # is operator prose. A client showing the maintenance screen with
            # an empty message falls back to its own translated default.
            message=product_settings.maintenance_message(runtime, fallback=""),
        ),
        signup_disabled=product_settings.is_signup_disabled(runtime),
    )
    return Envelope(data=data, meta=Meta(request_id=_request_id(request)))
