"""
Verifies T-203: one request produces one trace (HTTP server span -> DB query
span), an ARQ job wrapped in @trace_job gets its own root span with the
agreed attributes, and the OTLP exporter is only wired up when
OTEL_EXPORTER_OTLP_ENDPOINT is actually set.

The HTTP/DB span-tree test builds its own throwaway FastAPI app + SQLAlchemy
engine + TracerProvider, exactly like test_metrics.py's "isolated app +
isolated registry" -- app.main's own app/engine are already instrumented
against the real, process-wide global TracerProvider at import time (see
app.main's `configure_tracing(app, settings)` call), and OTel only allows
`set_tracer_provider` to succeed once per process, so a second, different
provider can't be swapped in globally without colliding with every other
test file that already imported app.main.

The ARQ job test can't rely on that same global provider either: it's built
with a 10%-ratio sampler (OTEL_TRACES_SAMPLER_ARG's default), so most
invocations wouldn't actually get recorded. trace_job looks up its tracer via
the ambient `opentelemetry.trace.get_tracer(...)` at call time, so the test
monkeypatches that lookup to point at a fresh, always-sampling provider
instead.
"""

import asyncio
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import SpanKind
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core import tracing as tracing_module
from app.core.config import Settings
from app.core.db import write_engine as app_engine
from app.core.tracing import _build_tracer_provider, trace_job
from app.main import app as real_app
from tests._database import DatabaseHandle
from tests._prod_settings import PROD_SAFE_OVERRIDES

FIXTURE_ENV_FILE = Path(__file__).parent.parent / ".env.test"


def _settings(**overrides: Any) -> Settings:  # noqa: ANN401 -- forwarded straight to Settings kwargs
    # Every non-dev invariant satisfied (tests._prod_settings); this file
    # only cares that the environment is not dev.
    return Settings(
        _env_file=FIXTURE_ENV_FILE,
        **{**PROD_SAFE_OVERRIDES, "ENVIRONMENT": "staging", **overrides},
    )


# --- _build_tracer_provider: OTLP exporter only wired up when set -----------------


def test_no_span_processor_when_otlp_endpoint_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    provider = _build_tracer_provider(_settings())

    assert provider._active_span_processor._span_processors == ()


def test_span_processor_added_when_otlp_endpoint_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4318")

    provider = _build_tracer_provider(_settings())

    assert len(provider._active_span_processor._span_processors) == 1


# --- wrong-method requests stay 405s -----------------------------------------------


def test_wrong_method_on_real_route_returns_405_not_a_500() -> None:
    # Regression test: opentelemetry-instrumentation-fastapi before 0.64b0
    # crashed with AttributeError on a partially-matched route (right path,
    # wrong method) because this app's build_router() (T-061) wraps included
    # sub-routers in a lazy _IncludedRouter with no `.path` attribute. The
    # pin in pyproject.toml is what keeps it fixed; this is what proves it.
    with TestClient(real_app, raise_server_exceptions=False) as client:
        response = client.post("/api/v1/config")

    assert response.status_code == 405


# --- HTTP -> DB span tree ----------------------------------------------------------


@pytest.fixture
def _isolated_sqlalchemy_instrumentor() -> Generator[None, None, None]:
    # SQLAlchemyInstrumentor is a singleton (BaseInstrumentor's
    # _is_instrumented_by_opentelemetry guard lives on the one shared
    # instance, not per engine=... kwarg) -- app.main's own configure_tracing
    # already instrumented app.core.db.write_engine.sync_engine with it at import
    # time, so a second .instrument() call for this test's own engine would
    # silently no-op without first undoing that. Restored afterwards so later
    # test modules see app.main's original wiring again.
    original_provider = trace.get_tracer_provider()
    SQLAlchemyInstrumentor().uninstrument(engine=app_engine.sync_engine)
    yield
    SQLAlchemyInstrumentor().instrument(
        engine=app_engine.sync_engine, tracer_provider=original_provider
    )


@pytest.fixture
def instrumented_engine(
    postgres_container: DatabaseHandle,
    _isolated_sqlalchemy_instrumentor: None,
) -> Generator[AsyncEngine, None, None]:
    # A plain (non-async) fixture on purpose: the test itself is sync and
    # drives the engine through TestClient's own event loop. An async fixture
    # would create asyncpg connections bound to pytest-asyncio's fixture loop
    # instead, which is closed before the sync test body -- and hence
    # TestClient's own loop -- ever runs.
    #
    # create_async_engine is imported here, not at module level: app.main
    # (imported by conftest.py at collection time, before any fixture runs)
    # already wrapped sqlalchemy.ext.asyncio.create_async_engine via its own
    # configure_tracing() call. A module-level `from sqlalchemy.ext.asyncio
    # import create_async_engine` would bind this test module's name to that
    # already-wrapped function forever, regardless of the uninstall above --
    # unwrap() restores the attribute on the sqlalchemy.ext.asyncio module,
    # not any name some other module already imported from it.
    from sqlalchemy.ext.asyncio import create_async_engine  # noqa: PLC0415

    url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    engine = create_async_engine(url)
    yield engine
    asyncio.run(engine.dispose())


def test_http_request_produces_server_span_with_db_child_span(
    instrumented_engine: AsyncEngine,
) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=ALWAYS_ON)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    SQLAlchemyInstrumentor().instrument(
        engine=instrumented_engine.sync_engine, tracer_provider=provider
    )

    app = FastAPI()

    @app.get("/ping")
    async def ping() -> dict[str, bool]:
        async with instrumented_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"ok": True}

    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    try:
        with TestClient(app) as client:
            response = client.get("/ping")
    finally:
        FastAPIInstrumentor.uninstrument_app(app)
        SQLAlchemyInstrumentor().uninstrument(engine=instrumented_engine.sync_engine)

    assert response.status_code == 200
    spans = exporter.get_finished_spans()
    server_span = next(s for s in spans if s.kind == SpanKind.SERVER)
    db_spans = [
        s
        for s in spans
        if s.kind == SpanKind.CLIENT and s.attributes is not None and s.attributes.get("db.system")
    ]
    assert db_spans, "expected at least one db.system-tagged CLIENT span"
    for db_span in db_spans:
        assert db_span.attributes["db.system"] == "postgresql"
        assert db_span.context is not None
        assert db_span.context.trace_id == server_span.context.trace_id
        assert db_span.parent is not None
        assert db_span.parent.span_id == server_span.context.span_id


# --- ARQ job root span --------------------------------------------------------------


async def test_trace_job_creates_root_span_with_job_attributes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=ALWAYS_ON)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(tracing_module.trace, "get_tracer", provider.get_tracer)

    calls: list[str] = []

    @trace_job
    async def sample_job(ctx: dict[str, Any], *, value: str) -> None:  # noqa: ARG001
        calls.append(value)

    await sample_job({"job_id": "job-abc", "job_try": 2}, value="hello")

    assert calls == ["hello"]
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "arq.sample_job"
    assert spans[0].attributes is not None
    assert spans[0].attributes["job.id"] == "job-abc"
    assert spans[0].attributes["job.try"] == 2
