"""process_avatar: an upload becomes a published avatar, or nothing.

Committed mode: the job opens its own sessions, as it does in the scanner
worker. The object store and the scanner are fakes; the re-encoding is real.
"""

import io
from typing import Any
from uuid import uuid4

import pytest
from arq.worker import Retry
from PIL import Image
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.exceptions import ExternalServiceUnavailableError
from app.modules.auth.models import AVATAR_KEY_PREFIX, User
from app.modules.storage import avatars as avatars_module
from app.modules.storage.scanner import ScanResult, ScanSource
from app.workers.jobs import avatars as job_module
from app.workers.jobs.avatars import process_avatar


class _FakeBackend:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def head(self, key: str) -> dict[str, Any] | None:
        return {"ContentLength": len(self.objects[key])} if key in self.objects else None

    async def read_prefix(self, key: str, num_bytes: int) -> bytes:
        return self.objects[key][:num_bytes]

    async def put(
        self,
        key: str,
        body: bytes,
        content_type: str,  # noqa: ARG002 -- StorageBackend's signature
        *,
        cache_control: str | None = None,  # noqa: ARG002 -- as above
    ) -> None:
        self.objects[key] = body

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)


class _Scanner:
    def __init__(self) -> None:
        self.result = ScanResult(clean=True)
        self.unavailable = False

    async def scan(self, source: ScanSource) -> ScanResult:  # noqa: ARG002 -- the verdict is set by the test
        if self.unavailable:
            raise ExternalServiceUnavailableError("virus_scanner")
        return self.result


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch, test_engine: AsyncEngine) -> _FakeBackend:
    fake = _FakeBackend()
    factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(job_module, "async_session_factory", factory)
    monkeypatch.setattr(job_module, "get_storage_backend", lambda: fake)
    monkeypatch.setattr(avatars_module, "get_storage_backend", lambda: fake)
    return fake


@pytest.fixture
def scanner(monkeypatch: pytest.MonkeyPatch) -> _Scanner:
    fake = _Scanner()
    monkeypatch.setattr(job_module, "get_virus_scanner", lambda: fake)
    return fake


@pytest.fixture
def ctx(redis: Redis) -> dict[str, Any]:
    return {"redis": redis, "job_try": 1}


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (800, 600), "orange").save(out, "PNG")
    return out.getvalue()


async def _user_with_upload(
    test_engine: AsyncEngine, backend: _FakeBackend, *, old_avatar: str | None = None
) -> tuple[User, str]:
    upload = f"incoming/avatars/{uuid4()}/{uuid4()}"
    backend.objects[upload] = _png()
    if old_avatar:
        backend.objects[old_avatar] = b"old"
    async with AsyncSession(test_engine, expire_on_commit=False) as session:
        user = User(
            email=f"job-{uuid4().hex[:8]}@example.com",
            avatar_object_key=old_avatar,
            avatar_pending_key=upload,
        )
        session.add(user)
        await session.commit()
    return user, upload


async def _reload(test_engine: AsyncEngine, user: User) -> User:
    async with AsyncSession(test_engine) as session:
        fresh = await session.get(User, user.id)
        assert fresh is not None
        return fresh


@pytest.mark.usefixtures("scanner")
async def test_a_clean_image_is_published_and_the_old_one_deleted(
    ctx: dict[str, Any], test_engine: AsyncEngine, backend: _FakeBackend
) -> None:
    old = f"{AVATAR_KEY_PREFIX}{uuid4()}.webp"
    user, upload = await _user_with_upload(test_engine, backend, old_avatar=old)

    await process_avatar(ctx, user_id=user.id, upload_key=upload)

    fresh = await _reload(test_engine, user)
    assert fresh.avatar_pending_key is None
    assert fresh.has_uploaded_avatar
    assert fresh.avatar_object_key != old
    assert list(backend.objects) == [fresh.avatar_object_key]
    assert Image.open(io.BytesIO(backend.objects[fresh.avatar_object_key])).format == "WEBP"


@pytest.mark.usefixtures("scanner")
async def test_a_superseded_upload_is_discarded(
    ctx: dict[str, Any], test_engine: AsyncEngine, backend: _FakeBackend
) -> None:
    """Two uploads in quick succession must not finish in the wrong order."""
    user, first = await _user_with_upload(test_engine, backend)
    second = f"incoming/avatars/{user.id}/{uuid4()}"
    async with AsyncSession(test_engine) as session:
        row = await session.get(User, user.id)
        assert row is not None
        row.avatar_pending_key = second
        await session.commit()

    await process_avatar(ctx, user_id=user.id, upload_key=first)

    fresh = await _reload(test_engine, user)
    assert fresh.avatar_pending_key == second
    assert fresh.avatar_object_key is None
    assert backend.objects == {}


@pytest.mark.parametrize("problem", ["infected", "not-an-image"])
async def test_a_rejected_upload_leaves_the_avatar_as_it_was(
    ctx: dict[str, Any],
    test_engine: AsyncEngine,
    backend: _FakeBackend,
    scanner: _Scanner,
    problem: str,
) -> None:
    old = f"{AVATAR_KEY_PREFIX}{uuid4()}.webp"
    user, upload = await _user_with_upload(test_engine, backend, old_avatar=old)
    if problem == "infected":
        scanner.result = ScanResult(clean=False, signature="Eicar-Test-Signature")
    else:
        backend.objects[upload] = b"%PDF-1.4 not a picture"

    await process_avatar(ctx, user_id=user.id, upload_key=upload)

    fresh = await _reload(test_engine, user)
    assert fresh.avatar_pending_key is None
    assert fresh.avatar_object_key == old
    assert list(backend.objects) == [old]


async def test_an_unavailable_scanner_is_retried_and_then_given_up_on(
    ctx: dict[str, Any], test_engine: AsyncEngine, backend: _FakeBackend, scanner: _Scanner
) -> None:
    """Given up on, rather than left pending: the profile page waits for the
    pending flag to clear, and a job arq drops would never clear it."""
    user, upload = await _user_with_upload(test_engine, backend)
    scanner.unavailable = True

    with pytest.raises(Retry):
        await process_avatar(ctx, user_id=user.id, upload_key=upload)
    assert (await _reload(test_engine, user)).avatar_pending_key == upload

    await process_avatar({**ctx, "job_try": 5}, user_id=user.id, upload_key=upload)
    assert (await _reload(test_engine, user)).avatar_pending_key is None
