"""
T-063: a single unauthenticated endpoint that tells every frontend surface
(web, mobile) exactly which modules are active and how the product is
branded. Frontend routes, nav items, and UI sections read this response
instead of hardcoding flags -- see packages/hooks/src/useModules.ts.
"""

from uuid import uuid4

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from app.core.envelope import Envelope, Meta
from app.core.i18n import get_default_locale, get_supported_locales
from app.core.module_loader import get_branding, get_modules, get_support_config
from app.core.support import SupportTool
from app.middleware.rate_limit import api_limit

router = APIRouter(tags=["config"])

_CACHE_CONTROL = "public, max-age=300"


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


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid4())


@router.get("/config", include_in_schema=True)
@api_limit
async def get_config(request: Request, response: Response) -> Envelope[ConfigResponse]:
    m = get_modules()
    b = get_branding()
    s = get_support_config()
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
    )
    return Envelope(data=data, meta=Meta(request_id=_request_id(request)))
