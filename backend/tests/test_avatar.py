"""Setting and reading a user's own avatar, out of a private bucket.

The uploader on the profile page stored a file and then did nothing with it:
`onUploaded` showed a toast, no column recorded which file it was, and
`avatar_url` (set only by OAuth) was rendered nowhere. So an avatar could be
uploaded, and never seen.

The bucket stays private, which is what shapes the design. There is no URL to
put on a user object, so the id of an uploaded file is exchanged for a
short-lived presigned URL by GET /auth/me/avatar, and the *server* resolves
the id to a storage key -- a client-supplied key would be a request to
presign any object in the bucket.

The check worth reading first is
`test_cannot_claim_a_file_uploaded_by_someone_else`.
"""

from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, UnprocessableEntityError
from app.modules.auth.models import User
from app.modules.auth.service import AuthService
from app.modules.storage import avatars as avatars_module
from app.modules.storage.avatars import (
    AVATAR_BUCKET,
    presign_own_avatar,
    resolve_own_avatar_key,
)
from app.modules.storage.models import StoredFile
from app.modules.tenancy.models import Organization

pytestmark = pytest.mark.module_dependent  # stored_file


@pytest.fixture
async def org(session: AsyncSession) -> Organization:
    """stored_file.org_id is a real foreign key, so a bare uuid4() will not
    do -- even though nothing under test reads the org."""
    organization = Organization(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    session.add(organization)
    await session.flush()
    return organization


@pytest.fixture
async def owner(session: AsyncSession) -> User:
    """A plain persisted user. `verified_user` lives in test_auth_router.py
    rather than conftest, and nothing here needs a verified one."""
    user = User(email=f"avatar-{uuid4().hex[:8]}@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    return user


async def _make_file(  # noqa: PLR0913 -- a row factory; every field is a test knob
    session: AsyncSession,
    *,
    uploaded_by: UUID,
    org_id: UUID,
    bucket: str = AVATAR_BUCKET,
    status: str = "ready",
    # An avatar is an attacker-supplied file handed to a browser with an
    # image content type, so it passes the same scan gate as every other
    # download. The default here is the post-scan state; the tests at the
    # bottom of this file cover what happens before one.
    virus_scan_result: str | None = "clean",
) -> StoredFile:
    stored = StoredFile(
        org_id=org_id,
        uploaded_by=uploaded_by,
        key=f"{org_id}/{bucket}/{uuid4()}/photo.jpg",
        filename="photo.jpg",
        content_type="image/jpeg",
        size_bytes=1024,
        bucket=bucket,
        status=status,
        virus_scan_result=virus_scan_result,
    )
    session.add(stored)
    await session.flush()
    return stored


async def test_resolves_the_key_of_the_users_own_ready_avatar(
    session: AsyncSession, owner: User, org: Organization
) -> None:
    stored = await _make_file(session, uploaded_by=owner.id, org_id=org.id)

    key = await resolve_own_avatar_key(session, user_id=owner.id, file_id=stored.id)

    assert key == stored.key


async def test_cannot_claim_a_file_uploaded_by_someone_else(
    session: AsyncSession, owner: User, org: Organization
) -> None:
    """The check the whole design rests on.

    Without it, setting `avatar_file_id` to any id in the system would have
    GET /auth/me/avatar presign that object -- and the bucket is private
    precisely so that cannot happen. It is also why the caller gets one
    undifferentiated failure: saying "not yours" rather than "not found"
    would confirm the file exists.
    """
    other = User(email=f"other-{uuid4().hex[:8]}@example.com", hashed_password="x")
    session.add(other)
    await session.flush()
    # uploaded_by is a real foreign key, so the other user has to exist --
    # which is the realistic case anyway: a file somebody actually uploaded.
    stored = await _make_file(session, uploaded_by=other.id, org_id=org.id)

    key = await resolve_own_avatar_key(session, user_id=owner.id, file_id=stored.id)

    assert key is None


async def test_ignores_a_file_outside_the_avatars_bucket(
    session: AsyncSession, owner: User, org: Organization
) -> None:
    """A document is not an avatar. Same private bucket family, different
    content-type allowlist, and no reason for the profile page to reach it."""
    stored = await _make_file(session, uploaded_by=owner.id, org_id=org.id, bucket="default")

    key = await resolve_own_avatar_key(session, user_id=owner.id, file_id=stored.id)

    assert key is None


async def test_ignores_a_file_that_never_finished_uploading(
    session: AsyncSession, owner: User, org: Organization
) -> None:
    """`pending` means presign was issued and confirm never succeeded, so
    there may be no object behind the key at all."""
    stored = await _make_file(session, uploaded_by=owner.id, org_id=org.id, status="pending")

    key = await resolve_own_avatar_key(session, user_id=owner.id, file_id=stored.id)

    assert key is None


async def test_ignores_a_file_that_does_not_exist(session: AsyncSession, owner: User) -> None:
    key = await resolve_own_avatar_key(session, user_id=owner.id, file_id=uuid4())

    assert key is None


async def test_finds_the_avatar_regardless_of_which_org_it_was_uploaded_in(
    session: AsyncSession, owner: User, org: Organization
) -> None:
    """The reason this lives outside StorageService.

    Every other query in the storage module filters on org_id, because a
    document belongs to the org. An avatar belongs to the person and follows
    them between orgs -- and the profile page showing it has no org in view
    at all. Scoping this by org would make an avatar vanish on switching.
    """
    stored = await _make_file(session, uploaded_by=owner.id, org_id=org.id)

    # Looked up with no org in hand whatsoever.
    key = await resolve_own_avatar_key(session, user_id=owner.id, file_id=stored.id)

    assert key == stored.key


async def test_setting_an_avatar_is_refused_when_file_storage_is_off(
    session: AsyncSession, owner: User, org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`file_storage` is optional and nothing requires it, so `stored_file`
    is absent in the minimum-surface configuration. Querying it there is a
    500; refusing is the honest answer, and there is nothing the client could
    legitimately be pointing at anyway."""
    stored = await _make_file(session, uploaded_by=owner.id, org_id=org.id)
    monkeypatch.setattr(
        "app.modules.auth.service.is_enabled", lambda name: name != "file_storage", raising=True
    )

    with pytest.raises(UnprocessableEntityError):
        await AuthService(session).resolve_avatar_change(owner.id, stored.id)


async def test_clearing_an_avatar_needs_no_storage_module(
    session: AsyncSession, owner: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removing an avatar is a write to `user`, nothing more -- it must not
    depend on a module that may be switched off."""
    monkeypatch.setattr(
        "app.modules.auth.service.is_enabled", lambda name: name != "file_storage", raising=True
    )

    assert await AuthService(session).resolve_avatar_change(owner.id, None) is None


async def test_an_avatar_still_being_scanned_can_be_chosen(
    session: AsyncSession, owner: User, org: Organization
) -> None:
    """Review R7 / plan D4: choosing is allowed before the verdict, serving is not.

    The profile page sets the avatar the moment the upload is confirmed, and
    no scanner finishes that fast -- requiring a verdict here failed nearly
    every first avatar upload. The scan gate moved to `presign_own_avatar`,
    which every read goes through (tests below).
    """
    stored = await _make_file(session, uploaded_by=owner.id, org_id=org.id, virus_scan_result=None)

    assert await resolve_own_avatar_key(session, user_id=owner.id, file_id=stored.id) == stored.key


async def test_an_avatar_that_could_not_be_scanned_cannot_be_chosen(
    session: AsyncSession, owner: User, org: Organization
) -> None:
    stored = await _make_file(session, uploaded_by=owner.id, org_id=org.id, status="failed")

    assert await resolve_own_avatar_key(session, user_id=owner.id, file_id=stored.id) is None


async def test_an_infected_avatar_does_not_resolve(
    session: AsyncSession, owner: User, org: Organization
) -> None:
    stored = await _make_file(
        session,
        uploaded_by=owner.id,
        org_id=org.id,
        status="infected",
        virus_scan_result="Eicar-Test-Signature",
    )

    assert await resolve_own_avatar_key(session, user_id=owner.id, file_id=stored.id) is None


# --- serving: the scan gate for avatars (review R7) ------------------------


class _FakeAvatarBackend:
    async def presign_download(
        self,
        key: str,
        expires_in: int = 3600,  # noqa: ARG002 -- StorageBackend's signature
        *,
        filename: str | None = None,  # noqa: ARG002 -- as above
    ) -> str:
        return f"https://fake.example/{key}"


@pytest.fixture
def fake_avatar_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(avatars_module, "get_storage_backend", _FakeAvatarBackend)


@pytest.mark.usefixtures("fake_avatar_backend")
async def test_a_clean_avatar_is_served(
    session: AsyncSession, owner: User, org: Organization
) -> None:
    stored = await _make_file(session, uploaded_by=owner.id, org_id=org.id)

    assert await presign_own_avatar(session, stored.key) == f"https://fake.example/{stored.key}"


@pytest.mark.usefixtures("fake_avatar_backend")
async def test_an_avatar_is_not_served_until_its_scan_is_clean(
    session: AsyncSession, owner: User, org: Organization
) -> None:
    """Chosen before the verdict, served only after it -- a 409 the page
    treats as "show initials for now", not as a missing avatar."""
    stored = await _make_file(session, uploaded_by=owner.id, org_id=org.id, virus_scan_result=None)

    with pytest.raises(ConflictError) as caught:
        await presign_own_avatar(session, stored.key)

    assert caught.value.message_key == "errors.file_scan_pending"


@pytest.mark.usefixtures("fake_avatar_backend")
async def test_an_avatar_found_infected_after_it_was_chosen_is_not_served(
    session: AsyncSession, owner: User, org: Organization
) -> None:
    stored = await _make_file(
        session,
        uploaded_by=owner.id,
        org_id=org.id,
        status="infected",
        virus_scan_result="Eicar-Test-Signature",
    )

    with pytest.raises(ConflictError) as caught:
        await presign_own_avatar(session, stored.key)

    assert caught.value.message_key == "errors.file_infected"


@pytest.mark.usefixtures("fake_avatar_backend")
async def test_a_deleted_avatar_is_not_found(session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await presign_own_avatar(session, "org/avatars/gone/me.png")
