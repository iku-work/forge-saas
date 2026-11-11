"""process_avatar: scan an uploaded avatar, re-encode it and publish it.

Runs on the scan queue, in the scanner worker (app.workers.main), so the image
is decoded there and never in the API process. The flow and why are in
app.modules.storage.avatars.

Only the user's latest upload is published. User.avatar_pending_key names it,
and a job whose upload is no longer that key discards it, so two uploads in
quick succession cannot finish in the wrong order. Whatever happens, the job
ends with the key cleared or replaced, which is what tells the profile page it
has finished.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, cast

from arq.worker import Retry
from sqlalchemy import select

from app.core.db import async_session_factory
from app.core.dependencies import invalidate_user_cache
from app.core.exceptions import ExternalServiceUnavailableError
from app.core.resilience import resilient
from app.core.tracing import trace_job
from app.modules.auth.models import User
from app.modules.storage.avatars import (
    MAX_AVATAR_BYTES,
    InvalidAvatarError,
    delete_avatar_objects,
    publish,
    reencode,
)
from app.modules.storage.backend import get_storage_backend
from app.modules.storage.scanner import (
    BytesSource,
    ScanLimitExceededError,
    ScannerError,
    ScanResult,
    get_virus_scanner,
)

if TYPE_CHECKING:
    from uuid import UUID

    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# arq's default max_tries. On the last one a scanner that is still down is a
# failure, not another retry: otherwise the job is dropped with the upload
# still pending, and the profile page waits for it forever.
_MAX_TRIES = 5
_RETRY_DEFER_SECONDS = 30


@resilient("virus_scanner")
async def _scan(raw: bytes) -> ScanResult:
    return await get_virus_scanner().scan(BytesSource(raw))


@trace_job
async def process_avatar(ctx: dict[str, Any], *, user_id: UUID, upload_key: str) -> None:
    redis: Redis = ctx["redis"]
    if not await _still_pending(user_id, upload_key):
        logger.info("process_avatar: user_id=%s upload superseded, discarding", user_id)
        await delete_avatar_objects(upload_key)
        return

    try:
        webp = await _checked_image(ctx, upload_key)
    except _RejectedError as exc:
        await _reject(redis, user_id, upload_key, str(exc))
        return
    new_key = await publish(webp)

    async with async_session_factory() as session:
        user = await _locked_user(session, user_id)
        if user is None or user.avatar_pending_key != upload_key:
            # Superseded while this one was being processed.
            await delete_avatar_objects(new_key, upload_key)
            return
        old_key = user.avatar_object_key
        user.avatar_object_key = new_key
        user.avatar_pending_key = None
        await session.commit()
    await invalidate_user_cache(redis, user_id)
    await delete_avatar_objects(old_key, upload_key)


class _RejectedError(Exception):
    """The upload will not become an avatar. The message says why, for the log."""


async def _checked_image(ctx: dict[str, Any], upload_key: str) -> bytes:
    """The upload, scanned and re-encoded. Raises _RejectedError, or Retry
    while the scanner is unavailable."""
    backend = get_storage_backend()
    if await backend.head(upload_key) is None:
        raise _RejectedError("the upload is gone")
    raw = await backend.read_prefix(upload_key, MAX_AVATAR_BYTES + 1)
    try:
        result = await _scan(raw)
    except ExternalServiceUnavailableError:
        if ctx.get("job_try", 1) < _MAX_TRIES:
            raise Retry(defer=_RETRY_DEFER_SECONDS) from None
        raise _RejectedError("the scanner stayed unavailable") from None
    except (ScannerError, ScanLimitExceededError) as exc:
        raise _RejectedError(f"the scanner failed: {exc}") from exc
    if not result.clean:
        raise _RejectedError(f"infected: {result.signature}")
    try:
        return await asyncio.to_thread(reencode, raw)
    except InvalidAvatarError as exc:
        raise _RejectedError(f"not a usable image: {exc}") from exc


async def _still_pending(user_id: UUID, upload_key: str) -> bool:
    async with async_session_factory() as session:
        user = await session.get(User, user_id)
        return user is not None and user.avatar_pending_key == upload_key


async def _locked_user(session: AsyncSession, user_id: UUID) -> User | None:
    model = cast("Any", User)
    result = await session.execute(select(User).where(model.id == user_id).with_for_update())
    return result.scalar_one_or_none()


async def _reject(redis: Redis, user_id: UUID, upload_key: str, reason: str) -> None:
    logger.warning("process_avatar: user_id=%s upload rejected: %s", user_id, reason)
    async with async_session_factory() as session:
        user = await _locked_user(session, user_id)
        if user is not None and user.avatar_pending_key == upload_key:
            user.avatar_pending_key = None
            await session.commit()
    await invalidate_user_cache(redis, user_id)
    await delete_avatar_objects(upload_key)
