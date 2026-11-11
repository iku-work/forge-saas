"""A user's own avatar: uploaded through the API, published at a public URL.

Until 2026-11 an avatar was a file in the user's organization, so a member
who could not upload files there could not change their own picture. It is
now the person's: staged by POST /auth/me/avatar, then scanned, re-encoded
and published by the scanner worker (tests/committed/test_avatar_job.py).

The check worth reading first is `test_reencoding_keeps_only_the_pixels`.
"""

import io
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import UnprocessableEntityError
from app.core.urls import public_object_url
from app.modules.auth.models import AVATAR_KEY_PREFIX, User
from app.modules.auth.schemas import UserPublic
from app.modules.auth.service import AuthService
from app.modules.auth.utils import create_access_token
from app.modules.storage import avatars as avatars_module
from app.modules.storage.avatars import (
    AVATAR_SIZE,
    MAX_AVATAR_BYTES,
    InvalidAvatarError,
    reencode,
)
from app.modules.storage.backend import PresignedUpload

_GPS_IFD = 0x8825
_GPS_LATITUDE = 2


def _jpeg_with_location() -> bytes:
    exif = Image.Exif()
    exif[0x010F] = "Phone maker"  # Make
    exif.get_ifd(_GPS_IFD)[_GPS_LATITUDE] = (60.0, 10.0, 30.0)
    out = io.BytesIO()
    Image.new("RGB", (1200, 900), "teal").save(out, "JPEG", exif=exif)
    return out.getvalue()


def _png(size: tuple[int, int], mode: str = "RGB") -> bytes:
    out = io.BytesIO()
    Image.new(mode, size).save(out, "PNG")
    return out.getvalue()


class _FakeBackend:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.presigned: list[tuple[str, str, int]] = []

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

    async def exists(self, key: str) -> bool:
        return key in self.objects

    async def presign_upload(
        self, key: str, content_type: str, max_bytes: int, expires_in: int = 600
    ) -> PresignedUpload:
        self.presigned.append((key, content_type, max_bytes))
        return PresignedUpload(
            url="https://storage.example/forge",
            fields={"key": key},
            key=key,
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
        )


@pytest.fixture
def fake_backend(monkeypatch: pytest.MonkeyPatch) -> _FakeBackend:
    backend = _FakeBackend()
    monkeypatch.setattr(avatars_module, "get_storage_backend", lambda: backend)
    return backend


@pytest.fixture
async def owner(session: AsyncSession) -> User:
    user = User(email=f"avatar-{uuid4().hex[:8]}@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    return user


def _bearer(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id, None, ())}"}


# --- re-encoding -----------------------------------------------------------


def test_reencoding_keeps_only_the_pixels() -> None:
    """A phone photo carries where it was taken. The published avatar must
    not: it is readable by anyone who has the URL."""
    raw = _jpeg_with_location()
    assert Image.open(io.BytesIO(raw)).getexif().get_ifd(_GPS_IFD)  # the fixture has it

    with Image.open(io.BytesIO(reencode(raw))) as published:
        assert published.format == "WEBP"
        assert published.size == (AVATAR_SIZE, AVATAR_SIZE)
        assert not published.getexif()
        assert "xmp" not in published.info
        assert "icc_profile" not in published.info


def test_a_phone_photo_is_turned_the_right_way_up() -> None:
    """Phones store the pixels sideways and an EXIF tag saying so. Dropping
    the tag without applying it would publish the avatar on its side."""
    image = Image.new("RGB", (2000, 1000), "red")
    image.paste((0, 0, 255), (1000, 0, 2000, 1000))
    exif = Image.Exif()
    exif[0x0112] = 6  # display rotated 90 degrees clockwise
    raw = io.BytesIO()
    image.save(raw, "JPEG", exif=exif)

    with Image.open(io.BytesIO(reencode(raw.getvalue()))) as published:
        upright = published.convert("RGB")
        pixel = upright.getpixel((AVATAR_SIZE // 2, 10))
        assert isinstance(pixel, tuple)
        red, _, blue = pixel
        assert red > 200 > blue  # the left half is now on top


def test_transparency_survives() -> None:
    with Image.open(io.BytesIO(reencode(_png((300, 300), "RGBA")))) as published:
        assert published.mode == "RGBA"


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"not an image at all", id="garbage"),
        pytest.param(_png((10, 10))[:40], id="truncated"),
        pytest.param(_png((6000, 5000), "1"), id="too-many-pixels"),
    ],
)
def test_refuses_what_it_cannot_safely_decode(raw: bytes) -> None:
    with pytest.raises(InvalidAvatarError):
        reencode(raw)


def test_refuses_a_gif() -> None:
    out = io.BytesIO()
    Image.new("P", (10, 10)).save(out, "GIF")

    with pytest.raises(InvalidAvatarError):
        reencode(out.getvalue())


# --- what clients see ------------------------------------------------------


def test_a_published_avatar_is_a_public_url_and_wins_over_the_oauth_picture() -> None:
    key = f"{AVATAR_KEY_PREFIX}{uuid4()}.webp"
    user = User(
        email="a@example.com", avatar_url="https://oauth.example/p.png", avatar_object_key=key
    )

    public = UserPublic.model_validate(user)

    assert public.avatar_url == public_object_url(key)
    assert public.has_uploaded_avatar
    assert not public.avatar_processing


def test_an_avatar_from_before_public_avatars_is_not_shown() -> None:
    """Those keys name private org files, which no longer have a URL."""
    user = User(
        email="a@example.com",
        avatar_url="https://oauth.example/p.png",
        avatar_object_key=f"{uuid4()}/avatars/{uuid4()}/me.png",
    )

    public = UserPublic.model_validate(user)

    assert public.avatar_url == "https://oauth.example/p.png"
    assert not public.has_uploaded_avatar


def test_an_upload_in_progress_is_reported_but_its_key_is_not() -> None:
    staged = "incoming/avatars/x/y"
    user = User(email="a@example.com", avatar_pending_key=staged)

    dumped = UserPublic.model_validate(user).model_dump()

    assert dumped["avatar_processing"] is True
    assert "incoming/" not in str(dumped)


def test_public_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "AWS_ENDPOINT_URL", "https://storage.example.com")
    monkeypatch.setattr(settings, "AWS_BUCKET_NAME", "forge")
    monkeypatch.setattr(settings, "PUBLIC_STORAGE_URL", "")
    assert public_object_url("public/a.webp") == "https://storage.example.com/forge/public/a.webp"

    monkeypatch.setattr(settings, "PUBLIC_STORAGE_URL", "https://cdn.example.com/")
    assert public_object_url("public/a.webp") == "https://cdn.example.com/public/a.webp"


# --- uploading -------------------------------------------------------------


async def test_uploads_are_refused_when_file_storage_is_off(
    session: AsyncSession, owner: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.modules.auth.service.is_enabled", lambda name: name != "file_storage", raising=True
    )

    with pytest.raises(UnprocessableEntityError):
        await AuthService(session).avatar_upload_form(owner.id, "image/png")


async def test_the_form_can_only_write_under_the_users_own_prefix(
    client: httpx.AsyncClient, owner: User, fake_backend: _FakeBackend
) -> None:
    response = await client.post(
        "/api/v1/auth/me/avatar/upload-form",
        headers=_bearer(owner),
        json={"content_type": "image/png"},
    )

    assert response.status_code == 200
    form = response.json()["data"]
    assert form["upload_key"].startswith(f"incoming/avatars/{owner.id}/")
    assert fake_backend.presigned == [(form["upload_key"], "image/png", MAX_AVATAR_BYTES)]


async def test_a_gif_gets_no_form(client: httpx.AsyncClient, owner: User) -> None:
    response = await client.post(
        "/api/v1/auth/me/avatar/upload-form",
        headers=_bearer(owner),
        json={"content_type": "image/gif"},
    )

    assert response.status_code == 422


async def test_setting_the_upload_queues_it_for_the_scanner(
    client: httpx.AsyncClient,
    owner: User,
    fake_backend: _FakeBackend,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    key = f"incoming/avatars/{owner.id}/{uuid4()}"
    fake_backend.objects[key] = _png((64, 64))

    response = await client.post(
        "/api/v1/auth/me/avatar", headers=_bearer(owner), json={"upload_key": key}
    )

    assert response.status_code == 202
    assert response.json()["data"]["avatar_processing"] is True
    assert owner.avatar_pending_key == key
    assert enqueued_jobs == [("process_avatar", {"user_id": owner.id, "upload_key": key})]


@pytest.mark.parametrize("whose", ["someone-else", "not-uploaded", "published", "traversal"])
async def test_only_the_users_own_arrived_upload_can_be_set(
    client: httpx.AsyncClient,
    owner: User,
    fake_backend: _FakeBackend,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
    whose: str,
) -> None:
    """Otherwise one user could publish another's upload as their own avatar."""
    key = {
        "someone-else": f"incoming/avatars/{uuid4()}/{uuid4()}",
        "not-uploaded": f"incoming/avatars/{owner.id}/{uuid4()}",
        "published": f"{AVATAR_KEY_PREFIX}{uuid4()}.webp",
        "traversal": f"incoming/avatars/{owner.id}/../{uuid4()}",
    }[whose]
    if whose != "not-uploaded":
        fake_backend.objects[key] = _png((64, 64))

    response = await client.post(
        "/api/v1/auth/me/avatar", headers=_bearer(owner), json={"upload_key": key}
    )

    assert response.status_code == 422
    assert owner.avatar_pending_key is None
    assert enqueued_jobs == []


async def test_removing_the_avatar_deletes_it(
    client: httpx.AsyncClient, session: AsyncSession, owner: User, fake_backend: _FakeBackend
) -> None:
    published = f"{AVATAR_KEY_PREFIX}{uuid4()}.webp"
    fake_backend.objects[published] = b"webp"
    owner.avatar_object_key = published
    await session.flush()

    response = await client.delete("/api/v1/auth/me/avatar", headers=_bearer(owner))

    assert response.status_code == 200
    assert response.json()["data"]["has_uploaded_avatar"] is False
    assert owner.avatar_object_key is None
    assert fake_backend.objects == {}


async def test_removing_when_there_is_no_avatar_touches_no_storage(
    client: httpx.AsyncClient, owner: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removal is a write to the user row; with nothing to delete it must not
    need the object store, which a product may not have."""

    def _no_storage() -> None:
        raise AssertionError("storage was reached")

    monkeypatch.setattr(avatars_module, "get_storage_backend", _no_storage)

    response = await client.delete("/api/v1/auth/me/avatar", headers=_bearer(owner))

    assert response.status_code == 200


async def test_a_storage_failure_does_not_undo_the_removal(
    client: httpx.AsyncClient,
    session: AsyncSession,
    owner: User,
    fake_backend: _FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The row no longer points at the image, which is what the user asked
    for. The object stays behind, unlisted and unreferenced, and the failure
    is logged rather than shown to them."""
    owner.avatar_object_key = f"{AVATAR_KEY_PREFIX}{uuid4()}.webp"
    await session.flush()

    async def _broken(key: str) -> None:
        raise RuntimeError(key)

    monkeypatch.setattr(fake_backend, "delete", _broken)

    response = await client.delete("/api/v1/auth/me/avatar", headers=_bearer(owner))

    assert response.status_code == 200
    assert owner.avatar_object_key is None
