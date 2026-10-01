"""Review OCT-01: two confirmations of one upload, in two real sessions.

Without a row lock, both read the row as pending; the later one could copy
replacement bytes over a served key the earlier one had confirmed and the
scan had cleared. With the lock the later one waits, then sees the committed
state and promotes nothing.
"""

import asyncio
from typing import Any
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.auth.models import User
from app.modules.storage import service as service_module
from app.modules.storage.keys import upload_key
from app.modules.storage.models import StoredFile
from app.modules.storage.service import StorageService
from app.modules.tenancy.models import Organization


class _Backend:
    """Just enough object store for confirm: the upload exists; the copy can
    be paused so a second confirmation gets its chance to race."""

    def __init__(self) -> None:
        self.copies = 0
        self.copy_started = asyncio.Event()
        self.release = asyncio.Event()

    async def head(self, key: str) -> dict[str, Any] | None:  # noqa: ARG002 -- protocol signature
        return {"ContentLength": 1024, "ETag": '"e"'}

    async def copy_if_match(self, source: str, destination: str, etag: str) -> None:  # noqa: ARG002
        self.copies += 1
        self.copy_started.set()
        await self.release.wait()

    async def delete(self, key: str) -> None:
        pass

    async def read_prefix(self, key: str, num_bytes: int) -> bytes:  # noqa: ARG002
        return b"%PDF-1.4\n"


async def test_a_second_confirmation_waits_and_promotes_nothing(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    redis_client: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _Backend()
    monkeypatch.setattr(service_module, "get_storage_backend", lambda: backend)
    org = Organization(name="Acme", slug=f"acme-{uuid4()}")
    user = User(email=f"u-{uuid4()}@example.com")
    session.add_all([org, user])
    await session.flush()
    key = f"{org.id}/default/{uuid4()}/f.pdf"
    row = StoredFile(
        org_id=org.id,
        uploaded_by=user.id,
        key=key,
        filename="f.pdf",
        content_type="application/pdf",
        size_bytes=1024,
    )
    session.add(row)
    await session.commit()
    assert upload_key(key).startswith("incoming/")

    async def confirm() -> StoredFile:
        async with session_factory() as s:
            confirmed = await StorageService(s, org.id, redis_client).confirm_upload(
                user.id, row.id
            )
            await s.commit()
            return confirmed

    first = asyncio.create_task(confirm())
    await backend.copy_started.wait()  # the first holds the row, mid-promotion
    second = asyncio.create_task(confirm())
    await asyncio.sleep(0.5)
    assert not second.done(), "the second confirmation read the row while the first held it"

    backend.release.set()
    results = await asyncio.gather(first, second)

    assert backend.copies == 1  # the second saw "ready" and promoted nothing
    assert [r.status for r in results] == ["ready", "ready"]
