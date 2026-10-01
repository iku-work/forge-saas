"""
T-240: the properties app.core.cache promises every caller -- versioned
payloads, degradation when Redis is unreachable, and lossless round-tripping
of a SQLModel row through JSON.

Uses the real Redis from conftest's container rather than a fake: the whole
point of the degradation tests is that they exercise the actual client's
error types, and a fake that raises a hand-picked exception would prove
nothing about what redis-py actually raises.
"""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core import cache, cache_keys
from app.modules.auth.models import User
from app.modules.tenancy.models import Organization


async def _loader_returning(payload: cache.JsonDict | None) -> cache.JsonDict | None:
    return payload


async def test_round_trip_hits_the_loader_once(redis: Redis) -> None:
    key = f"test:{uuid4()}"
    calls = 0

    async def loader() -> cache.JsonDict:
        nonlocal calls
        calls += 1
        return {"answer": 42}

    assert await cache.cached_json(redis, key, loader) == {"answer": 42}
    assert await cache.cached_json(redis, key, loader) == {"answer": 42}
    assert calls == 1


async def test_loader_returning_none_is_not_negatively_cached(redis: Redis) -> None:
    key = f"test:{uuid4()}"
    assert await cache.cached_json(redis, key, lambda: _loader_returning(None)) is None
    # A row created a moment later must be visible immediately, not after a TTL.
    assert await cache.cached_json(redis, key, lambda: _loader_returning({"a": 1})) == {"a": 1}


async def test_version_mismatch_reads_as_a_miss(redis: Redis) -> None:
    key = f"test:{uuid4()}"
    await redis.set(key, json.dumps({"v": cache.CACHE_VERSION + 1, "d": {"stale": True}}))

    assert await cache.read_json(redis, key) is None
    # ...and a miss, not an exception: the loader's value wins and is rewritten.
    assert await cache.cached_json(redis, key, lambda: _loader_returning({"fresh": 1})) == {
        "fresh": 1
    }
    assert await cache.read_json(redis, key) == {"fresh": 1}


@pytest.mark.parametrize(
    "raw",
    [
        "not json at all",
        json.dumps(["a", "list"]),  # valid JSON, wrong envelope shape
        json.dumps({"d": {"no": "version"}}),
        json.dumps({"v": cache.CACHE_VERSION, "d": "not a dict"}),
    ],
)
async def test_unparseable_or_malformed_payload_reads_as_a_miss(redis: Redis, raw: str) -> None:
    key = f"test:{uuid4()}"
    await redis.set(key, raw)
    assert await cache.read_json(redis, key) is None


class _DeadRedis:
    """Every method raises what redis-py raises when it can't reach a server."""

    def _die(self, *_args: Any, **_kwargs: Any) -> Any:  # noqa: ANN401 -- stands in for arbitrary Redis methods
        raise RedisConnectionError("Error 111 connecting to localhost:6379. Connection refused.")

    async def get(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        return self._die(*args, **kwargs)

    async def set(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        return self._die(*args, **kwargs)

    async def delete(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        return self._die(*args, **kwargs)

    def scan_iter(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        return self._die(*args, **kwargs)


async def test_reads_fall_through_to_the_loader_when_redis_is_down() -> None:
    dead: Any = _DeadRedis()
    calls = 0

    async def loader() -> cache.JsonDict:
        nonlocal calls
        calls += 1
        return {"from": "db"}

    assert await cache.cached_json(dead, "any-key", loader) == {"from": "db"}
    assert await cache.cached_json(dead, "any-key", loader) == {"from": "db"}
    # No caching happened, so the DB was consulted both times -- degraded, not broken.
    assert calls == 2


async def test_writes_and_invalidation_do_not_raise_when_redis_is_down() -> None:
    dead: Any = _DeadRedis()
    org_id = uuid4()

    await cache.write_json(dead, "any-key", {"a": 1})
    await cache.invalidate(dead, "any-key")
    await cache.invalidate_pattern(dead, "any:*")
    await cache.invalidate_org_entitlements(dead, org_id)


async def test_invalidate_with_no_keys_is_a_noop(redis: Redis) -> None:
    # redis-py's DEL rejects an empty key list; the helper must absorb the
    # case rather than every caller checking first.
    await cache.invalidate(redis)


async def test_hydrate_restores_uuid_and_datetime_fields() -> None:
    user = User(
        email="round@trip.example",
        hashed_password="x",
        locale="fi",
        email_verified_at=datetime.now(UTC),
    )
    payload = cache.dump(user)

    # The JSON form really is strings -- this is what used to leak into
    # query parameters as a str id.
    assert isinstance(payload["id"], str)
    assert isinstance(payload["email_verified_at"], str)

    restored = cache.hydrate(User, payload)
    assert restored.id == user.id
    assert restored.email == user.email
    assert restored.locale == "fi"
    assert restored.email_verified_at == user.email_verified_at
    assert restored.deleted_at is None


async def test_hydrate_preserves_jsonb_columns() -> None:
    org = Organization(name="Acme", slug=f"acme-{uuid4()}", settings={"theme": "dark", "seats": 5})
    restored = cache.hydrate(Organization, cache.dump(org))

    assert restored.id == org.id
    assert restored.settings == {"theme": "dark", "seats": 5}
    assert restored.plan_id is None


async def test_invalidate_org_entitlements_clears_plan_and_every_flag_key(
    redis: Redis,
) -> None:
    org_id = uuid4()
    plan_key = cache_keys.org_plan_key(org_id)
    flag_key = cache_keys.feature_flag_key(org_id, "storage_gb")
    all_key = cache_keys.feature_flags_all_key(org_id)
    other_org_key = cache_keys.org_plan_key(uuid4())
    for key in (plan_key, flag_key, all_key, other_org_key):
        await redis.set(key, "1")

    await cache.invalidate_org_entitlements(redis, org_id)

    assert await redis.get(plan_key) is None
    assert await redis.get(flag_key) is None
    assert await redis.get(all_key) is None
    assert await redis.get(other_org_key) is not None  # scoped to one org
