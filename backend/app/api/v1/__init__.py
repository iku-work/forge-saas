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
    from app.api.v1.support import router as support_router  # noqa: PLC0415
    from app.modules.auth.oauth import router as oauth_router  # noqa: PLC0415
    from app.modules.auth.router import router as auth_router  # noqa: PLC0415
    from app.modules.auth.totp import router as totp_router  # noqa: PLC0415
    from app.modules.auth.webauthn import router as webauthn_router  # noqa: PLC0415
    from app.modules.tenancy.router import router as tenancy_router  # noqa: PLC0415

    router.include_router(config_router)
    router.include_router(support_router)
    router.include_router(auth_router)
    router.include_router(oauth_router)
    router.include_router(totp_router)
    router.include_router(webauthn_router)
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

    if modules.feature_flags.enabled:
        from app.modules.feature_flags.router import (  # noqa: PLC0415
            router as feature_flags_router,
        )

        router.include_router(feature_flags_router)

    if modules.admin.enabled:
        from app.modules.admin.router import router as admin_router  # noqa: PLC0415

        router.include_router(admin_router)

    if modules.public_api.enabled:
        from app.api.v1.public_openapi import router as public_openapi_router  # noqa: PLC0415
        from app.modules.api_keys.router import router as api_keys_router  # noqa: PLC0415

        router.include_router(api_keys_router)
        # T-314's published contract. Under the same toggle as the management
        # routes rather than always-on: with `public_api` off a key cannot
        # authenticate at all, and a contract nobody can call is worse than no
        # contract.
        router.include_router(public_openapi_router)

    if modules.outbound_webhooks.enabled:
        from app.modules.webhooks.router import router as webhooks_router  # noqa: PLC0415

        router.include_router(webhooks_router)

    if modules.enterprise_sso.enabled:
        from app.modules.sso.router import router as sso_router  # noqa: PLC0415

        router.include_router(sso_router)

    # `marketing` never gets a block: it gates the apps/marketing build, not a
    # router.
    #
    # Note what the `public_api` block above does *not* register: the two
    # routes an API key can actually call (T-311) belong to `tenancy` and are
    # always registered. That is the design -- a key is a second way to
    # authenticate against endpoints the product already serves, not a
    # parallel API -- and it is why the key's own auth dependency checks
    # `is_enabled("public_api")` rather than trusting the routing table (see
    # app/core/principal.py).

    return router
