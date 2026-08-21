"""
Verifies T-112's StoredFileRepository: the 60s-cached total_bytes_for_org
read (cached value used until explicitly invalidated, only "ready" +
non-deleted rows counted), count_files_for_org, and list_cursor_filtered's
optional bucket filter on top of BaseRepository's usual cursor semantics.

Same real-Postgres + real-Redis testcontainers (via conftest's session-scoped
`engine`/`redis_container`) as every other repository test file -- StoredFile
already has a real migration (T-110), so no throwaway _TABLES subset is
needed here.
"""

from uuid import uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.repository import UserRepository
from app.modules.storage.repository import StoredFileRepository
from app.modules.tenancy.repository import OrganizationRepository

# T-291: this file needs file_storage enabled -- its routes, its tables, or
# both. CI's module-matrix job deselects it in the combination that turns
# every optional module off.
pytestmark = pytest.mark.module_dependent


@pytest.fixture
def redis(_redis_client: Redis) -> Redis:
    return _redis_client


@pytest.fixture
async def org(session: AsyncSession):
    return await OrganizationRepository(session).create(name="Acme", slug=f"acme-{uuid4().hex[:8]}")


@pytest.fixture
async def uploader_id(session: AsyncSession):
    user = await UserRepository(session).create(email=f"uploader-{uuid4()}@example.com")
    return user.id


async def _make_file(
    repo: StoredFileRepository,
    uploader_id,
    *,
    status: str = "ready",
    size_bytes: int = 1000,
    bucket: str = "default",
):
    return await repo.create(
        uploaded_by=uploader_id,
        key=f"k/{uuid4()}",
        filename="f.txt",
        content_type="text/plain",
        size_bytes=size_bytes,
        bucket=bucket,
        status=status,
    )


async def test_total_bytes_for_org_sums_only_ready_files(
    session: AsyncSession, redis: Redis, org, uploader_id
) -> None:
    repo = StoredFileRepository(session, org.id, redis)
    await _make_file(repo, uploader_id, status="ready", size_bytes=1000)
    await _make_file(repo, uploader_id, status="ready", size_bytes=2000)
    await _make_file(repo, uploader_id, status="pending", size_bytes=5000)

    total = await repo.total_bytes_for_org()

    assert total == 3000


async def test_total_bytes_for_org_excludes_soft_deleted(
    session: AsyncSession, redis: Redis, org, uploader_id
) -> None:
    repo = StoredFileRepository(session, org.id, redis)
    deleted = await _make_file(repo, uploader_id, status="ready", size_bytes=1000)
    await repo.soft_delete(deleted.id)
    await repo.invalidate_total_bytes_cache()

    total = await repo.total_bytes_for_org()

    assert total == 0


async def test_total_bytes_for_org_is_cached_until_invalidated(
    session: AsyncSession, redis: Redis, org, uploader_id
) -> None:
    repo = StoredFileRepository(session, org.id, redis)
    await _make_file(repo, uploader_id, status="ready", size_bytes=1000)

    first = await repo.total_bytes_for_org()
    await _make_file(repo, uploader_id, status="ready", size_bytes=9000)
    cached = await repo.total_bytes_for_org()

    assert first == 1000
    assert cached == 1000  # second insert not yet reflected -- still cached

    await repo.invalidate_total_bytes_cache()
    fresh = await repo.total_bytes_for_org()

    assert fresh == 10000


async def test_count_files_for_org_excludes_soft_deleted(
    session: AsyncSession, redis: Redis, org, uploader_id
) -> None:
    repo = StoredFileRepository(session, org.id, redis)
    await _make_file(repo, uploader_id)
    deleted = await _make_file(repo, uploader_id)
    await repo.soft_delete(deleted.id)

    count = await repo.count_files_for_org()

    assert count == 1


async def test_list_cursor_filtered_by_bucket(
    session: AsyncSession, redis: Redis, org, uploader_id
) -> None:
    repo = StoredFileRepository(session, org.id, redis)
    avatar = await _make_file(repo, uploader_id, bucket="avatars")
    await _make_file(repo, uploader_id, bucket="default")

    files, next_cursor = await repo.list_cursor_filtered(bucket="avatars")

    assert [f.id for f in files] == [avatar.id]
    assert next_cursor is None


async def test_list_cursor_filtered_without_bucket_returns_all(
    session: AsyncSession, redis: Redis, org, uploader_id
) -> None:
    repo = StoredFileRepository(session, org.id, redis)
    await _make_file(repo, uploader_id, bucket="avatars")
    await _make_file(repo, uploader_id, bucket="default")

    files, _next_cursor = await repo.list_cursor_filtered()

    assert len(files) == 2
