"""An organization's uploaded logo (visual overhaul follow-up).

The org-scoped counterpart of test_avatar.py: a logo is chosen by file id
through PATCH /orgs/{id}, resolved by the server to a key in that org's
`logos` bucket, and served only as a short-lived presigned URL once its scan
is clean. The check worth reading first is
`test_cannot_use_another_orgs_file_as_a_logo`.
"""

from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import User
from app.modules.auth.permissions import MemberRole
from app.modules.auth.utils import create_access_token
from app.modules.storage import logos as logos_module
from app.modules.storage.logos import LOGO_BUCKET
from app.modules.storage.models import StoredFile
from app.modules.tenancy.models import Membership, Organization

pytestmark = pytest.mark.module_dependent  # stored_file


class _FakeBackend:
    async def presign_download(
        self,
        key: str,
        expires_in: int = 3600,  # noqa: ARG002 -- StorageBackend's signature
        *,
        filename: str | None = None,  # noqa: ARG002 -- as above
    ) -> str:
        return f"https://fake.example/{key}"


@pytest.fixture
def fake_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logos_module, "get_storage_backend", _FakeBackend)


async def _org_with_owner(session: AsyncSession, name: str) -> tuple[Organization, User]:
    owner = User(email=f"owner-{uuid4().hex[:8]}@example.com", hashed_password="x")
    org = Organization(name=name, slug=f"{name.lower()}-{uuid4().hex[:8]}")
    session.add_all([owner, org])
    await session.flush()
    session.add(Membership(user_id=owner.id, org_id=org.id, role=MemberRole.OWNER))
    await session.flush()
    return org, owner


async def _make_file(
    session: AsyncSession,
    *,
    org_id: UUID,
    uploaded_by: UUID,
    bucket: str = LOGO_BUCKET,
    virus_scan_result: str | None = "clean",
) -> StoredFile:
    stored = StoredFile(
        org_id=org_id,
        uploaded_by=uploaded_by,
        key=f"{org_id}/{bucket}/{uuid4()}/logo.png",
        filename="logo.png",
        content_type="image/png",
        size_bytes=1024,
        bucket=bucket,
        status="ready",
        virus_scan_result=virus_scan_result,
    )
    session.add(stored)
    await session.flush()
    return stored


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id, None, ())}"}


@pytest.mark.usefixtures("fake_backend")
async def test_a_logo_is_chosen_served_and_cleared(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    org, owner = await _org_with_owner(session, "Acme")
    stored = await _make_file(session, org_id=org.id, uploaded_by=owner.id)

    chosen = await client.patch(
        f"/api/v1/orgs/{org.id}", json={"logo_file_id": str(stored.id)}, headers=_headers(owner)
    )
    assert chosen.status_code == 200
    assert chosen.json()["data"]["has_uploaded_logo"] is True
    assert "logo_object_key" not in chosen.json()["data"]

    served = await client.get(f"/api/v1/orgs/{org.id}/logo", headers=_headers(owner))
    assert served.status_code == 200
    assert served.json()["data"]["url"] == f"https://fake.example/{stored.key}"

    cleared = await client.patch(
        f"/api/v1/orgs/{org.id}", json={"logo_file_id": None}, headers=_headers(owner)
    )
    assert cleared.json()["data"]["has_uploaded_logo"] is False
    gone = await client.get(f"/api/v1/orgs/{org.id}/logo", headers=_headers(owner))
    assert gone.status_code == 404


async def test_cannot_use_another_orgs_file_as_a_logo(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """Otherwise any file id in the system could be presigned as a logo --
    and the answer is the same 422 as any unusable file, so it confirms
    nothing about the other org's file."""
    org, owner = await _org_with_owner(session, "Acme")
    other_org, other_owner = await _org_with_owner(session, "Other")
    theirs = await _make_file(session, org_id=other_org.id, uploaded_by=other_owner.id)

    response = await client.patch(
        f"/api/v1/orgs/{org.id}", json={"logo_file_id": str(theirs.id)}, headers=_headers(owner)
    )

    assert response.status_code == 422
    assert response.json()["error"]["message_key"] == "errors.logo_file_invalid"


async def test_a_document_is_not_a_logo(session: AsyncSession, client: httpx.AsyncClient) -> None:
    org, owner = await _org_with_owner(session, "Acme")
    document = await _make_file(session, org_id=org.id, uploaded_by=owner.id, bucket="default")

    response = await client.patch(
        f"/api/v1/orgs/{org.id}", json={"logo_file_id": str(document.id)}, headers=_headers(owner)
    )

    assert response.status_code == 422


@pytest.mark.usefixtures("fake_backend")
async def test_a_logo_is_not_served_until_its_scan_is_clean(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    """Chosen before the verdict (the page sets it as soon as the upload is
    confirmed), served only after it."""
    org, owner = await _org_with_owner(session, "Acme")
    stored = await _make_file(session, org_id=org.id, uploaded_by=owner.id, virus_scan_result=None)
    await client.patch(
        f"/api/v1/orgs/{org.id}", json={"logo_file_id": str(stored.id)}, headers=_headers(owner)
    )

    response = await client.get(f"/api/v1/orgs/{org.id}/logo", headers=_headers(owner))

    assert response.status_code == 409


async def test_a_logo_cannot_be_set_with_file_storage_off(
    session: AsyncSession, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.modules.tenancy.service.is_enabled", lambda name: name != "file_storage"
    )
    org, owner = await _org_with_owner(session, "Acme")

    response = await client.patch(
        f"/api/v1/orgs/{org.id}", json={"logo_file_id": str(uuid4())}, headers=_headers(owner)
    )

    assert response.status_code == 422


async def test_a_logo_whose_file_is_gone_is_not_found(
    session: AsyncSession, client: httpx.AsyncClient
) -> None:
    org, owner = await _org_with_owner(session, "Acme")
    org.logo_object_key = f"{org.id}/{LOGO_BUCKET}/missing/logo.png"
    await session.flush()

    response = await client.get(f"/api/v1/orgs/{org.id}/logo", headers=_headers(owner))

    assert response.status_code == 404
