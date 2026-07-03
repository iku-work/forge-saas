"""Verifies T-142's Redis-backed jti blocklist in isolation from the
impersonation flow that consumes it."""

from uuid import uuid4

import pytest
from redis.asyncio import Redis

from app.core.token_blocklist import block_jti, is_jti_blocked


@pytest.fixture
def redis(_redis_client: Redis) -> Redis:
    return _redis_client


async def test_is_jti_blocked_false_for_unknown_jti(redis: Redis) -> None:
    assert await is_jti_blocked(redis, uuid4()) is False


async def test_block_jti_then_is_jti_blocked_true(redis: Redis) -> None:
    jti = uuid4()

    await block_jti(redis, jti, ttl_seconds=60)

    assert await is_jti_blocked(redis, jti) is True


async def test_block_jti_with_zero_ttl_is_a_no_op(redis: Redis) -> None:
    jti = uuid4()

    await block_jti(redis, jti, ttl_seconds=0)

    assert await is_jti_blocked(redis, jti) is False
