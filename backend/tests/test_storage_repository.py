"""
Verifies T-112's StoredFileRepository: the total_bytes_for_org quota read
(ready rows plus live reservations, never soft-deleted ones),
count_files_for_org, and list_cursor_filtered's optional bucket filter on
top of BaseRepository's usual cursor semantics.

That read was cached in Redis for sixty seconds, and a test here asserted
the stale value was returned until something invalidated it -- which
documented the bug rather than the behaviour. It is the only read the
storage quota gate makes, so a minute of staleness meant a minute in which
every presign in the fleet could be authorised against space that was
already spent. Both the cache and that test are gone; `lock_for_quota_check`
is what makes concurrent presigns safe now.

Same real-Postgres + real-Redis testcontainers (via conftest's session-scoped
`engine`) as every other repository test file -- StoredFile
already has a real migration (T-110), so no throwaway _TABLES subset is
needed here.
"""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models._mixins import utcnow
from app.modules.auth.repository import UserRepository
from app.modules.storage.quota import UPLOAD_RESERVATION_WINDOW
from app.modules.storage.repository import StoredFileRepository
from app.modules.tenancy.repository import OrganizationRepository

# T-291: this file needs file_storage enabled -- its routes, its tables, or
# both. CI's module-matrix job deselects it in the combination that turns
# every optional module off.
pytestmark = pytest.mark.module_dependent


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


async def test_total_bytes_counts_ready_files_and_live_reservations(
    session: AsyncSession, org, uploader_id
) -> None:
    """A pending row is space someone has already been promised.

    Counting only `ready` was the finding: N presigns each checked against
    the same free bytes, because none of them could see the others.
    """
    repo = StoredFileRepository(session, org.id)
    await _make_file(repo, uploader_id, status="ready", size_bytes=1000)
    await _make_file(repo, uploader_id, status="ready", size_bytes=2000)
    await _make_file(repo, uploader_id, status="pending", size_bytes=5000)

    total = await repo.total_bytes_for_org()

    assert total == 8000


async def test_total_bytes_stops_counting_a_lapsed_reservation(
    session: AsyncSession, org, uploader_id
) -> None:
    """...but only until the reservation window closes.

    The other direction matters just as much: a presign nobody confirmed
    must stop holding the org's allowance down, or counting reservations
    turns a correctness fix into a way to fill someone's quota by opening
    tabs.
    """
    repo = StoredFileRepository(session, org.id)
    stale = await _make_file(repo, uploader_id, status="pending", size_bytes=5000)
    stale.created_at = utcnow() - UPLOAD_RESERVATION_WINDOW - timedelta(minutes=1)
    session.add(stale)
    await session.flush()

    assert await repo.total_bytes_for_org() == 0


async def test_total_bytes_for_org_excludes_soft_deleted(
    session: AsyncSession, org, uploader_id
) -> None:
    repo = StoredFileRepository(session, org.id)
    deleted = await _make_file(repo, uploader_id, status="ready", size_bytes=1000)
    await repo.soft_delete(deleted.id)

    total = await repo.total_bytes_for_org()

    assert total == 0


async def test_total_bytes_reflects_a_write_immediately(
    session: AsyncSession, org, uploader_id
) -> None:
    """The inverse of the test this replaces, which asserted a sixty-second
    stale window on the storage quota gate and called it caching."""
    repo = StoredFileRepository(session, org.id)
    await _make_file(repo, uploader_id, status="ready", size_bytes=1000)

    assert await repo.total_bytes_for_org() == 1000

    await _make_file(repo, uploader_id, status="ready", size_bytes=9000)

    assert await repo.total_bytes_for_org() == 10000


async def test_count_files_for_org_excludes_soft_deleted(
    session: AsyncSession, org, uploader_id
) -> None:
    repo = StoredFileRepository(session, org.id)
    await _make_file(repo, uploader_id)
    deleted = await _make_file(repo, uploader_id)
    await repo.soft_delete(deleted.id)

    count = await repo.count_files_for_org()

    assert count == 1


async def test_list_cursor_filtered_by_bucket(session: AsyncSession, org, uploader_id) -> None:
    repo = StoredFileRepository(session, org.id)
    avatar = await _make_file(repo, uploader_id, bucket="avatars")
    await _make_file(repo, uploader_id, bucket="default")

    files, next_cursor = await repo.list_cursor_filtered(bucket="avatars")

    assert [f.id for f in files] == [avatar.id]
    assert next_cursor is None


async def test_list_cursor_filtered_without_bucket_returns_all(
    session: AsyncSession, org, uploader_id
) -> None:
    repo = StoredFileRepository(session, org.id)
    await _make_file(repo, uploader_id, bucket="avatars")
    await _make_file(repo, uploader_id, bucket="default")

    files, _next_cursor = await repo.list_cursor_filtered()

    assert len(files) == 2
