"""
Verifies T-202: /metrics is scrapeable and Prometheus-formatted, the custom
collectors (DB pool, ARQ queue depth, active sessions) are present and
resilient to a down Redis, and label cardinality stays on route templates
rather than raw paths.

No Postgres/Redis testcontainers needed here -- the custom gauges introspect
the async engine's in-memory pool metadata (no connection made) and read
Redis lazily via a sync client that's expected to fail closed (0.0) when
unreachable, exactly like the health checks in test_health.py.
"""

import pytest
import redis
from arq.constants import default_queue_name
from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY, CollectorRegistry
from prometheus_fastapi_instrumentator import Instrumentator

from app.core import metrics as metrics_module
from app.main import app


def test_metrics_endpoint_returns_prometheus_text_format() -> None:
    with TestClient(app) as client:
        response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "forge_db_pool_size" in response.text
    assert "forge_db_pool_used" in response.text
    assert "forge_arq_queue_depth" in response.text
    assert "forge_active_sessions" in response.text
    # Published to Redis by the key-rotation job's own process (T-245), read
    # back here -- same split as forge_active_sessions.
    assert "forge_reencrypt_remaining" in response.text
    assert "forge_stripe_webhook_seconds" in response.text
    assert "forge_emails_sent_total" in response.text
    assert "forge_push_sent_total" in response.text
    # Powers T-204's api-overview.json "in-flight requests" panel.
    assert "http_requests_inprogress" in response.text


def test_metrics_endpoint_excluded_from_openapi_schema() -> None:
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()

    assert "/metrics" not in schema["paths"]


def test_db_pool_gauges_reflect_engine_pool_without_connecting() -> None:
    with TestClient(app) as client:
        response = client.get("/metrics")

    lines = response.text.splitlines()
    size_line = next(line for line in lines if line.startswith("forge_db_pool_size"))
    assert float(size_line.split()[-1]) == metrics_module.write_engine.pool.size()


def test_arq_queue_depth_key_matches_arq_default_queue_name() -> None:
    assert default_queue_name == metrics_module._ARQ_QUEUE_KEY


class _UnreachableRedis:
    def zcard(self, *_args: object, **_kwargs: object) -> int:
        raise redis.exceptions.ConnectionError("boom")

    def get(self, *_args: object, **_kwargs: object) -> None:
        raise redis.exceptions.ConnectionError("boom")


def test_gauges_fail_closed_to_zero_when_redis_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(metrics_module, "_get_sync_redis", _UnreachableRedis)

    assert metrics_module._read_arq_queue_depth() == 0.0
    assert metrics_module._read_active_sessions() == 0.0
    assert metrics_module._read_reencrypt_remaining() == 0.0


def test_route_templates_used_not_raw_paths_for_high_cardinality_labels() -> None:
    # A throwaway app + isolated registry, rather than the real app's /metrics
    # (which shares the process-wide default CollectorRegistry) -- this
    # isolates the assertion to the exact should_group_untemplated/handler
    # behavior init_metrics relies on, with no risk of colliding with metrics
    # already registered by app.main's own init_metrics(app) call.
    isolated_app = FastAPI()

    @isolated_app.get("/items/{item_id}")
    def get_item(item_id: str) -> dict[str, str]:
        return {"item_id": item_id}

    Instrumentator(registry=CollectorRegistry()).instrument(isolated_app).expose(
        isolated_app, endpoint="/metrics", include_in_schema=False
    )

    with TestClient(isolated_app) as client:
        client.get("/items/11111111-1111-1111-1111-111111111111")
        client.get("/items/22222222-2222-2222-2222-222222222222")
        metrics_response = client.get("/metrics")

    body = metrics_response.text
    assert 'handler="/items/{item_id}"' in body
    assert "11111111-1111-1111-1111-111111111111" not in body
    assert "22222222-2222-2222-2222-222222222222" not in body


def test_default_registry_has_no_duplicate_forge_metric_names() -> None:
    # Guards against a future accidental double-registration of a custom
    # collector (would raise ValueError at import time, not at test time --
    # this just documents/pins that importing app.main is the only place
    # these get created).
    #
    # Keyed by (name, labels), not name alone: a labeled counter like
    # forge_breaker_open_total{integration=...} (T-206) legitimately emits
    # one same-named sample per label combination once other tests in the
    # full suite have touched more than one integration's breaker -- that's
    # multiple samples of one registered metric, not a double-registration.
    samples = [
        (sample.name, tuple(sorted(sample.labels.items())))
        for metric_family in REGISTRY.collect()
        for sample in metric_family.samples
        if sample.name.startswith("forge_") and sample.name.endswith(("_total", "_count"))
    ]
    assert len(samples) == len(set(samples))
