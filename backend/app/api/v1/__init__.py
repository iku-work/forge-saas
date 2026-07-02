from fastapi import APIRouter

from app.core.module_loader import get_modules


def build_router() -> APIRouter:
    """
    Routers for optional modules are only included when their module is
    enabled (T-061) -- a disabled module's routes never exist, rather than
    existing but rejecting requests. Imports are lazy and per-branch so a
    disabled module's router (and its transitive imports) never even loads.
    """
    modules = get_modules()
    router = APIRouter(prefix="/api/v1")

    # -- Always-on (never optional) ------------------------------------------
    from app.api.v1.config import router as config_router  # noqa: PLC0415
    from app.modules.auth.oauth import router as oauth_router  # noqa: PLC0415
    from app.modules.auth.router import router as auth_router  # noqa: PLC0415
    from app.modules.auth.totp import router as totp_router  # noqa: PLC0415
    from app.modules.tenancy.router import router as tenancy_router  # noqa: PLC0415

    router.include_router(config_router)
    router.include_router(auth_router)
    router.include_router(oauth_router)
    router.include_router(totp_router)
    router.include_router(tenancy_router)

    # -- Optional -------------------------------------------------------------
    if modules.billing.enabled:
        from app.modules.billing.router import router as billing_router  # noqa: PLC0415
        from app.modules.billing.webhooks import (  # noqa: PLC0415
            router as billing_webhooks_router,
        )

        router.include_router(billing_router)
        router.include_router(billing_webhooks_router)

    if modules.gdpr.enabled:
        from app.modules.gdpr.router import router as gdpr_router  # noqa: PLC0415

        router.include_router(gdpr_router)

    if modules.notifications.enabled:
        from app.modules.notifications.sse import router as notifications_router  # noqa: PLC0415

        router.include_router(notifications_router)

    if modules.file_storage.enabled:
        from app.modules.storage.router import router as storage_router  # noqa: PLC0415

        router.include_router(storage_router)

    return router
