"""
The 60s read-through JSON cache behind §0.9's "Redis on hot reads" (T-240).

Three properties every caller gets for free, each of which was a hand-rolled
(and inconsistently applied) detail before this module existed:

*Versioned payloads.* Everything written here is wrapped in
`{"v": CACHE_VERSION, "d": ...}`. A payload written by an older shape of the
code -- a renamed column, a changed dict layout -- reads back as a cache
*miss*, never as an exception or, worse, a half-populated model. Bump
CACHE_VERSION in the same commit as any change to what a cached payload
contains, and the whole cache retires itself on deploy.

*Degradation.* A Redis that is down, timing out, or refusing connections
turns every read here into a miss and every write into a no-op (logged once
per call at WARNING). The caller falls through to its DB loader and the
request succeeds -- the same "an unavailable dependency degrades, it doesn't
500" posture app.core.resilience takes for outbound HTTP (T-206).

*Hydration.* SQLModel `table=True` classes skip pydantic's coercion on
model_validate/model_validate_json, so a row round-tripped through JSON comes
back with `id` as a `str` rather than a `UUID` and every datetime as a
string -- silently, until something downstream binds one into a UUID-typed
query parameter. `hydrate()` coerces field-by-field, driven by the model's
own annotations rather than a hand-maintained list of field names (which is
what app.core.dependencies used to carry, and which would have gone stale the
first time someone added a datetime column to User).

Cache keys never appear here as literals -- they all come from
app.core.cache_keys, which also carries the invalidation matrix.
"""

import json
import types
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Union, cast, get_args, get_origin
from uuid import UUID

from loguru import logger
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlmodel import SQLModel

from app.core import cache_keys

# Bump on any change to the *shape* of a cached payload (see module docstring).
CACHE_VERSION = 1
DEFAULT_TTL_SECONDS = 60

type JsonDict = dict[str, Any]


async def read_json(redis: Redis, key: str) -> JsonDict | None:
    """The cached payload for `key`, or None on a miss -- where "miss" also
    covers unparseable JSON, a payload from a different CACHE_VERSION, and
    Redis being unreachable."""
    try:
        raw = await redis.get(key)
    except RedisError as exc:
        logger.warning("cache.read_failed: key={} error={}", key, exc)
        return None
    if raw is None:
        return None

    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("cache.unparseable_payload: key={}", key)
        return None

    if not isinstance(envelope, dict) or envelope.get("v") != CACHE_VERSION:
        # Deliberately a miss, not an error: a rolling deploy has both
        # versions of the code reading the same Redis for a few minutes.
        return None
    payload = envelope.get("d")
    if not isinstance(payload, dict):
        return None
    return payload


async def write_json(
    redis: Redis, key: str, payload: JsonDict, ttl: int = DEFAULT_TTL_SECONDS
) -> None:
    try:
        await redis.set(key, json.dumps({"v": CACHE_VERSION, "d": payload}), ex=ttl)
    except RedisError as exc:
        logger.warning("cache.write_failed: key={} error={}", key, exc)


async def cached_json(
    redis: Redis,
    key: str,
    loader: Callable[[], Awaitable[JsonDict | None]],
    ttl: int = DEFAULT_TTL_SECONDS,
) -> JsonDict | None:
    """Read-through: the cached payload for `key`, else `loader()`'s result,
    which is cached for `ttl` seconds. A loader returning None (the row is
    gone) is never cached -- negative caching would keep a just-created row
    invisible for a full TTL."""
    cached = await read_json(redis, key)
    if cached is not None:
        return cached

    payload = await loader()
    if payload is not None:
        await write_json(redis, key, payload, ttl)
    return payload


async def invalidate(redis: Redis, *keys: str) -> None:
    if not keys:
        return
    try:
        await redis.delete(*keys)
    except RedisError as exc:
        # Swallowed for the same reason a failed read is: a write path must
        # not 500 because Redis blinked. The blast radius is bounded by the
        # TTL -- at worst a stale read for the rest of the minute.
        logger.warning("cache.invalidate_failed: keys={} error={}", keys, exc)


async def invalidate_pattern(redis: Redis, pattern: str) -> None:
    try:
        keys = [key async for key in redis.scan_iter(match=pattern)]
    except RedisError as exc:
        logger.warning("cache.scan_failed: pattern={} error={}", pattern, exc)
        return
    await invalidate(redis, *keys)


async def invalidate_org_entitlements(redis: Redis, org_id: UUID) -> None:
    """Everything derived from the org's plan: the resolved Subscription+Plan
    itself and every feature-flag answer computed from it. The two are always
    busted together -- a flag cached True from a plan the org no longer has is
    exactly the production bug T-240 exists to prevent -- so no caller should
    ever delete one without the other, and nothing else does."""
    await invalidate(redis, cache_keys.org_plan_key(org_id))
    await invalidate_pattern(redis, cache_keys.feature_flags_pattern(org_id))


def dump[T: SQLModel](instance: T) -> JsonDict:
    """A row as a JSON-safe dict. model_dump_json (not model_dump) so UUID/
    datetime/Decimal fields go through pydantic's own JSON encoders rather
    than json.dumps's."""
    # cast, not a bare return: model_dump_json is typed `str`, so json.loads
    # of it is Any -- a model always serializes to a JSON object.
    return cast("JsonDict", json.loads(instance.model_dump_json()))


def hydrate[T: SQLModel](model: type[T], payload: JsonDict) -> T:
    """The inverse of dump(): a detached model instance with UUID/datetime
    fields coerced back from their JSON string forms (see module docstring).

    The instance is *detached* -- mutating it and flushing the session will
    not persist anything. Every write path in this codebase goes through a
    repository that re-fetches by id, so that is the intended shape; anything
    that needs an attached row must not read it from the cache.
    """
    coerced = {
        name: _coerce(field.annotation, payload[name])
        for name, field in model.model_fields.items()
        if name in payload
    }
    return model(**coerced)


def _coerce(annotation: Any, value: object) -> object:  # noqa: ANN401 -- pydantic FieldInfo.annotation is Any
    if not isinstance(value, str):
        return value
    for candidate in _unwrap_optional(annotation):
        if candidate is UUID:
            return UUID(value)
        if candidate is datetime:
            return datetime.fromisoformat(value)
    return value


def _unwrap_optional(annotation: Any) -> tuple[Any, ...]:  # noqa: ANN401 -- see _coerce
    """`UUID | None` -> (UUID, NoneType); anything else -> (annotation,)."""
    if get_origin(annotation) in (Union, types.UnionType):
        return get_args(annotation)
    return (annotation,)
