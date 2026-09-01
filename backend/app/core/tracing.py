"""
OpenTelemetry traces (T-203): one request produces one trace -- HTTP server
span -> DB query spans -> Redis spans -- and every ARQ job gets its own root
span. The exporter is OTLP (vendor-neutral -- Tempo, Jaeger, and Honeycomb all
speak it), switched on by the standard OTEL_EXPORTER_OTLP_ENDPOINT env var
(read directly via os.environ, same as OTEL_TRACES_SAMPLER_ARG below, rather
than added to app.core.config.Settings -- these are OTel's own conventions,
not app-specific config).

The TracerProvider and every instrumentor are wired up regardless of whether
the endpoint is set: only the OTLP span processor is conditional. With no
processor attached, sampled spans are created and immediately discarded --
nothing is ever exported and nothing does network I/O, so an unset endpoint
(the dev default) is a no-op in every way that matters, without special-
casing instrumentation setup itself.
"""

import functools
import os
from collections.abc import Callable, Coroutine
from typing import Any

import opentelemetry.instrumentation.fastapi as _fastapi_instrumentation
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

from app.core.config import Settings
from app.core.db import read_engine, read_replica_configured, write_engine
from app.core.sentry import _app_release  # reuse T-201's package-version lookup

_OTEL_EXPORTER_OTLP_ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"
_OTEL_TRACES_SAMPLER_ARG_ENV = "OTEL_TRACES_SAMPLER_ARG"
_DEFAULT_SAMPLE_RATIO = 0.1


def _sample_ratio() -> float:
    # nosemgrep: semgrep.no-os-environ -- OTel's own convention, not app config.
    # The rule's advice ("add it to Settings") is wrong here: the SDK reads
    # this variable from the environment itself, so a mirrored setting could
    # disagree with it and the env var would still win. See the module
    # docstring.
    return float(os.environ.get(_OTEL_TRACES_SAMPLER_ARG_ENV, _DEFAULT_SAMPLE_RATIO))


def _build_tracer_provider(settings: Settings) -> TracerProvider:
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": "forge-backend",
                "service.version": _app_release(),
                "deployment.environment": settings.ENVIRONMENT,
            }
        ),
        sampler=ParentBased(TraceIdRatioBased(_sample_ratio())),
    )
    # nosemgrep: semgrep.no-os-environ -- see above; OTLPSpanExporter() below
    # takes no arguments precisely because it reads this same variable itself.
    if os.environ.get(_OTEL_EXPORTER_OTLP_ENDPOINT_ENV):
        # OTLPSpanExporter() with no args reads OTEL_EXPORTER_OTLP_ENDPOINT
        # (and friends) itself -- no need to pass the value along here.
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    return provider


_fastapi_partial_match_bug_patched = False


def _patch_fastapi_partial_match_bug() -> None:
    """
    Works around opentelemetry-instrumentation-fastapi==0.58b0's
    _get_route_details(): on a partially-matched route (right path, wrong
    method -- what would otherwise be a plain 405) it does
    `route = starlette_route.path` with no try/except, unlike the identical
    fully-matched branch just above it in the same function. This app's
    build_router() (T-061) wraps every included sub-router in a lazy
    _IncludedRouter with no `path` attribute (the same FastAPI 0.137.2 quirk
    already noted for prometheus-fastapi-instrumentator above in
    pyproject.toml) -- so any wrong-method request crashes FastAPIInstrumentor's
    ASGI middleware with an unhandled AttributeError instead of the app
    producing its own 405.

    Fixed upstream in opentelemetry-instrumentation-fastapi>=0.64b0, but that
    version requires opentelemetry-api>=1.43.0, which conflicts with
    semgrep's opentelemetry-api~=1.37.0 pin (see pyproject.toml) -- so this
    patches just the one broken line rather than bumping the package.
    """
    global _fastapi_partial_match_bug_patched  # noqa: PLW0603 -- guards the module-level singleton below
    if _fastapi_partial_match_bug_patched:
        return
    # Deliberately patching a private, unexported function on a third-party
    # module -- see the docstring above for why.
    original_get_route_details = _fastapi_instrumentation._get_route_details  # noqa: SLF001

    def _get_route_details_safe(scope: dict[str, Any]) -> str | None:
        try:
            return original_get_route_details(scope)  # type: ignore[no-any-return, no-untyped-call]
        except AttributeError:
            return scope.get("path")

    _fastapi_instrumentation._get_route_details = _get_route_details_safe  # noqa: SLF001
    _fastapi_partial_match_bug_patched = True


def configure_tracing(app: FastAPI, settings: Settings) -> TracerProvider:
    provider = _build_tracer_provider(settings)
    trace.set_tracer_provider(provider)

    _patch_fastapi_partial_match_bug()
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    # "on the async engine's sync core": SQLAlchemyInstrumentor hooks
    # before/after_cursor_execute, which fire on the sync Engine/Connection
    # machinery asyncpg still drives under the hood -- engine.sync_engine,
    # not the AsyncEngine wrapper, is what actually carries those events.
    SQLAlchemyInstrumentor().instrument(engine=write_engine.sync_engine, tracer_provider=provider)
    # T-246: the replica is a second engine with its own connection pool, and
    # an untraced one would make replica-routed reads simply vanish from a
    # trace. Guarded because read_engine *is* write_engine unless a replica is
    # configured -- instrumenting the same sync engine twice double-reports
    # every query.
    if read_replica_configured():
        SQLAlchemyInstrumentor().instrument(
            engine=read_engine.sync_engine, tracer_provider=provider
        )
    RedisInstrumentor().instrument(tracer_provider=provider)
    return provider


def trace_job[T](
    func: Callable[..., Coroutine[Any, Any, T]],
) -> Callable[..., Coroutine[Any, Any, T]]:
    """
    Wraps an ARQ job coroutine in its own root span `arq.<job_name>`.

    Applied at each job's definition site (not to the WorkerSettings.functions
    list in app.workers.main) so the wrapped function *is* what every caller
    -- main.py, tests, enqueue_job's string lookup by __qualname__ -- imports
    and refers to; wrapping only inside main.py's list would leave the
    original, untraced function as the object everything else still imports.
    """
    job_name = func.__name__

    @functools.wraps(func)
    async def wrapper(
        ctx: dict[str, Any],
        *args: Any,  # noqa: ANN401 -- forwarded straight to func's own **kwargs job signature
        **kwargs: Any,  # noqa: ANN401 -- forwarded straight to func's own **kwargs job signature
    ) -> T:
        tracer = trace.get_tracer("app.workers")
        with tracer.start_as_current_span(f"arq.{job_name}") as span:
            span.set_attribute("job.id", str(ctx.get("job_id", "")))
            span.set_attribute("job.try", ctx.get("job_try", 0))
            return await func(ctx, *args, **kwargs)

    return wrapper
