"""
Redis-backed single-token revocation for access-token `jti`s (T-142): ending
an impersonation session must invalidate that one access token immediately,
unlike app.modules.auth.repository.SessionRepository's refresh-token
revocation, which revokes a whole session/device on next refresh. An
ordinary (non-impersonation) access token is never blocklisted -- it simply
expires on its own short TTL, so this stays a narrow, opt-in mechanism
rather than a general revocation list every login has to pay for.
"""

from uuid import UUID

from redis.asyncio import Redis

_KEY_PREFIX = "jwt:blocklist:"


def _key(jti: UUID) -> str:
    return f"{_KEY_PREFIX}{jti}"


async def block_jti(redis: Redis, jti: UUID, ttl_seconds: int) -> None:
    if ttl_seconds > 0:
        await redis.set(_key(jti), "1", ex=ttl_seconds)


async def is_jti_blocked(redis: Redis, jti: UUID) -> bool:
    return bool(await redis.exists(_key(jti)))
