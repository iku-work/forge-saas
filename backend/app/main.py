from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,  # noqa: TC002 — evaluated at runtime as a local annotation below
)

from app.api.v1 import build_router
from app.core.config import get_settings, validate_stripe_keys
from app.core.db import dispose_engine, write_engine
from app.core.encrypted_type import get_shared_fernet
from app.core.handlers import register_exception_handlers
from app.core.i18n import load_translations
from app.core.jobs import dispose_pool as dispose_jobs_pool
from app.core.logging import configure_logging
from app.core.metrics import init_metrics
from app.core.module_loader import get_modules
from app.core.sentry import init_sentry
from app.core.tracing import configure_tracing
from app.middleware.impersonation import ImpersonationContextMiddleware
from app.middleware.locale import LocaleMiddleware
from app.middleware.rate_limit import register_rate_limiting
from app.middleware.request_id import RequestIDMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    get_shared_fernet()  # validates ENCRYPTION_KEY at boot — fails loudly, not on first DB write
    app.state.engine = write_engine
    app.state.redis = Redis.from_url(str(settings.REDIS_URL))
    try:
        yield
    finally:
        await dispose_engine()
        await app.state.redis.aclose()
        await dispose_jobs_pool()


settings = get_settings()
configure_logging(settings=settings)
init_sentry(settings)  # before any middleware so its exceptions are reportable too
get_modules()  # validates product.config.py dependency rules before any router is registered
validate_stripe_keys(settings)  # T-065: billing-disabled products may omit Stripe keys entirely
load_translations()  # T-068: populates the i18n cache before any request can hit t()

# T-225/ASVS V14.3.2: interactive API docs are a dev convenience, not
# something prod should expose -- they leak the full schema (including
# internal-only fields/response shapes) to anyone who requests them.
_docs_enabled = settings.ENVIRONMENT == "dev"
app = FastAPI(
    title="ForgeSaaS",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)
register_exception_handlers(app)
register_rate_limiting(app)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(ImpersonationContextMiddleware)
app.add_middleware(LocaleMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    # Authorization and X-Org-ID aren't in Starlette's CORS-safelisted
    # header set, so a cross-origin browser request carrying either (i.e.
    # every authenticated, org-scoped request the web app makes) fails
    # preflight without this -- Content-Type is included too since it's only
    # safelisted for a few MIME types, not the "application/json" every
    # mutating request here sends.
    allow_headers=["Authorization", "Content-Type", "X-Org-ID"],
)
app.include_router(build_router())
init_metrics(app)
configure_tracing(app, settings)


def _custom_openapi() -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    # FastAPI auto-documents 422s with its own HTTPValidationError shape
    # ({"detail": [...]}) ), but app.core.handlers.validation_error_handler
    # actually returns the {data, error, meta} envelope (§0.4) -- left as-is,
    # every fuzzed validation failure in T-050's schemathesis contract test
    # would fail response-schema conformance against a schema that no route
    # actually implements.
    schemas = schema.get("components", {}).get("schemas", {})
    if "Envelope_NoneType_" in schemas:
        error_schema = {"$ref": "#/components/schemas/Envelope_NoneType_"}
        for path_item in schema["paths"].values():
            for operation in path_item.values():
                responses = operation.get("responses", {})
                if "422" in responses:
                    responses["422"]["content"] = {"application/json": {"schema": error_schema}}
    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = _custom_openapi  # type: ignore[method-assign]  # rewriting the 422 schema above


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": "0.1.0"}


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


async def check_database_ready() -> bool:
    engine: AsyncEngine = app.state.engine
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 — health probe must report "not ready" on any failure mode
        return False
    return True


async def check_redis_ready() -> bool:
    redis: Redis = app.state.redis
    try:
        await redis.ping()
    except Exception:  # noqa: BLE001 — health probe must report "not ready" on any failure mode
        return False
    return True


@app.get("/health/ready")
async def health_ready(
    db_ready: Annotated[bool, Depends(check_database_ready)],
    redis_ready: Annotated[bool, Depends(check_redis_ready)],
) -> JSONResponse:
    if db_ready and redis_ready:
        return JSONResponse(status_code=200, content={"status": "ok"})
    return JSONResponse(status_code=503, content={"status": "unavailable"})
