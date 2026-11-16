"""What is inside one org, for the admin org page: members, files, opening a
file, removing one.

Opening needs a recent sign-in, works only on a file scanned clean, renders
images and PDF in the browser and downloads anything else, and is audited.
Removing needs a reason, is audited, and tells the org's owners.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models._mixins import utcnow
from app.modules.admin import router as admin_router
from app.modules.audit.models import AuditEvent
from app.modules.auth import amr
from app.modules.auth.models import User
from app.modules.auth.permissions import MemberRole
from app.modules.auth.repository import SessionRepository, UserRepository
from app.modules.auth.utils import create_access_token, generate_refresh_token, hash_password
from app.modules.notifications.models import Notification
from app.modules.storage.models import StoredFile
from app.modules.tenancy.models import Membership, Organization
from tests._auth import session_token

# Files and the owners' notification come from optional modules.
pytestmark = pytest.mark.module_dependent

_REASON = "Reported as inappropriate by two members (ticket #5133)"


async def _user(session: AsyncSession, **overrides: Any) -> User:  # noqa: ANN401 -- forwarded
    fields: dict[str, Any] = {
        "email": f"user-{uuid4()}@example.com",
        "hashed_password": hash_password("Correct-Horse9"),
        "email_verified_at": datetime.now(UTC),
        **overrides,
    }
    user = await UserRepository(session).create(**fields)
    await session.commit()
    return user


async def _org(
    session: AsyncSession, **members: MemberRole
) -> tuple[Organization, dict[str, User]]:
    org = Organization(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    session.add(org)
    await session.flush()
    users: dict[str, User] = {}
    for name, role in members.items():
        users[name] = await _user(session)
        session.add(Membership(user_id=users[name].id, org_id=org.id, role=role))
    await session.commit()
    return org, users


async def _file(  # noqa: PLR0913 -- every field a test varies, as keywords
    session: AsyncSession,
    org: Organization,
    uploader: User,
    *,
    content_type: str = "image/png",
    status: str = "ready",
    verdict: str | None = "clean",
) -> StoredFile:
    stored = StoredFile(
        org_id=org.id,
        uploaded_by=uploader.id,
        key=f"{org.id}/{uuid4().hex}",
        filename="holiday.png" if content_type == "image/png" else "notes.txt",
        content_type=content_type,
        size_bytes=2048,
        status=status,
        virus_scan_result=verdict,
    )
    session.add(stored)
    await session.commit()
    return stored


async def _admin(client: httpx.AsyncClient, session: AsyncSession) -> User:
    admin = await _user(session, is_superadmin=True, totp_enabled=True)
    client.headers["Authorization"] = f"Bearer {await session_token(session, admin)}"
    return admin


async def _stale_admin(client: httpx.AsyncClient, session: AsyncSession) -> None:
    """A superadmin whose sign-in is hours old: past the step-up window."""
    admin = await _user(session, is_superadmin=True, totp_enabled=True)
    _plain, token_hash = generate_refresh_token()
    signed_in = utcnow() - timedelta(hours=3)
    row = await SessionRepository(session).create(
        user_id=admin.id,
        token_hash=token_hash,
        device_label=None,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=utcnow() + timedelta(days=30),
        auth_time=signed_in,
        amr=(amr.PASSWORD, amr.TOTP),
    )
    await session.commit()
    token = create_access_token(admin.id, None, (), auth_time=signed_in, sid=row.id)
    client.headers["Authorization"] = f"Bearer {token}"


async def _audit(session: AsyncSession, resource_id: UUID) -> list[AuditEvent]:
    m = cast("Any", AuditEvent)
    stmt = select(AuditEvent).where(m.resource_id == str(resource_id)).order_by(m.ts)
    return list((await session.execute(stmt)).scalars().all())


# --- members --------------------------------------------------------------------


async def test_members_are_listed_a_page_at_a_time(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _admin(client, session)
    org, users = await _org(session, owner=MemberRole.OWNER, member=MemberRole.MEMBER)

    first = await client.get(f"/api/v1/admin/orgs/{org.id}/members", params={"limit": 1})
    cursor = first.json()["meta"]["next_cursor"]
    second = await client.get(
        f"/api/v1/admin/orgs/{org.id}/members", params={"limit": 1, "after": cursor}
    )

    rows = first.json()["data"] + second.json()["data"]
    assert {(row["email"], row["role"]) for row in rows} == {
        (users["owner"].email, "owner"),
        (users["member"].email, "member"),
    }
    assert second.json()["meta"]["next_cursor"] is None


async def test_a_member_cannot_list_them(client: httpx.AsyncClient, session: AsyncSession) -> None:
    org, users = await _org(session, owner=MemberRole.OWNER)
    client.headers["Authorization"] = f"Bearer {await session_token(session, users['owner'])}"

    response = await client.get(f"/api/v1/admin/orgs/{org.id}/members")

    assert response.status_code == 403


# --- files ----------------------------------------------------------------------


async def test_files_show_one_scan_state_each(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _admin(client, session)
    org, users = await _org(session, owner=MemberRole.OWNER)
    uploader = users["owner"]
    await _file(session, org, uploader, status="pending", verdict=None)
    await _file(session, org, uploader, status="ready", verdict=None)
    await _file(session, org, uploader, status="ready", verdict="clean")
    await _file(session, org, uploader, status="infected", verdict="infected")

    response = await client.get(f"/api/v1/admin/orgs/{org.id}/files")

    rows = response.json()["data"]
    assert sorted(row["status"] for row in rows) == ["clean", "infected", "scanning", "uploading"]
    assert {row["uploaded_by_email"] for row in rows} == {uploader.email}


async def test_files_404_without_the_storage_module(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _admin(client, session)
    org, _ = await _org(session, owner=MemberRole.OWNER)
    monkeypatch.setattr(admin_router, "is_enabled", lambda module: module != "file_storage")

    response = await client.get(f"/api/v1/admin/orgs/{org.id}/files")

    assert response.status_code == 404


# --- preview --------------------------------------------------------------------


async def test_an_image_opens_in_the_browser_and_is_audited(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin = await _admin(client, session)
    org, users = await _org(session, owner=MemberRole.OWNER)
    stored = await _file(session, org, users["owner"], content_type="image/png")

    response = await client.post(f"/api/v1/admin/orgs/{org.id}/files/{stored.id}/preview")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["inline"] is True
    query = parse_qs(urlparse(data["url"]).query)
    assert query["response-content-disposition"][0].startswith("inline;")
    assert query["response-content-type"] == ["image/png"]
    [event] = await _audit(session, stored.id)
    assert event.action == "admin.file.preview"
    assert event.actor_id == admin.id


async def test_anything_else_downloads(client: httpx.AsyncClient, session: AsyncSession) -> None:
    await _admin(client, session)
    org, users = await _org(session, owner=MemberRole.OWNER)
    stored = await _file(session, org, users["owner"], content_type="text/plain")

    response = await client.post(f"/api/v1/admin/orgs/{org.id}/files/{stored.id}/preview")

    data = response.json()["data"]
    assert data["inline"] is False
    query = parse_qs(urlparse(data["url"]).query)
    assert query["response-content-disposition"][0].startswith("attachment;")
    assert "response-content-type" not in query


@pytest.mark.parametrize(
    ("status", "verdict"),
    [("ready", None), ("infected", "infected"), ("failed", None), ("pending", None)],
)
async def test_only_a_clean_file_opens(
    client: httpx.AsyncClient, session: AsyncSession, status: str, verdict: str | None
) -> None:
    await _admin(client, session)
    org, users = await _org(session, owner=MemberRole.OWNER)
    stored = await _file(session, org, users["owner"], status=status, verdict=verdict)

    response = await client.post(f"/api/v1/admin/orgs/{org.id}/files/{stored.id}/preview")

    assert response.status_code == 409


async def test_opening_needs_a_recent_sign_in(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _stale_admin(client, session)
    org, users = await _org(session, owner=MemberRole.OWNER)
    stored = await _file(session, org, users["owner"])

    response = await client.post(f"/api/v1/admin/orgs/{org.id}/files/{stored.id}/preview")

    assert response.status_code == 403
    assert response.json()["error"]["message_key"] == "errors.step_up_auth_required"


async def test_another_orgs_file_is_not_found(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _admin(client, session)
    org, users = await _org(session, owner=MemberRole.OWNER)
    other, _ = await _org(session, owner=MemberRole.OWNER)
    stored = await _file(session, org, users["owner"])

    response = await client.post(f"/api/v1/admin/orgs/{other.id}/files/{stored.id}/preview")

    assert response.status_code == 404


# --- remove ---------------------------------------------------------------------


async def test_removing_a_file_tells_the_owners_and_is_audited(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin = await _admin(client, session)
    org, users = await _org(
        session, owner=MemberRole.OWNER, co_owner=MemberRole.OWNER, member=MemberRole.MEMBER
    )
    stored = await _file(session, org, users["member"])
    stored_id, org_id, admin_id = stored.id, org.id, admin.id
    owner_ids = {users["owner"].id, users["co_owner"].id}

    response = await client.post(
        f"/api/v1/admin/orgs/{org_id}/files/{stored_id}/delete", json={"reason": _REASON}
    )

    assert response.status_code == 200
    session.expire_all()
    removed = await session.get(StoredFile, stored_id)
    assert removed is not None
    assert removed.deleted_at is not None
    events = {e.action: e for e in await _audit(session, stored_id)}
    assert events["admin.file.delete"].actor_id == admin_id
    assert events["admin.file.delete"].diff == {"reason": _REASON, "filename": "holiday.png"}
    m = cast("Any", Notification)
    told = (
        await session.execute(select(m.user_id).where(m.type == "file_removed", m.org_id == org_id))
    ).scalars()
    assert set(told) == owner_ids


async def test_removing_needs_a_reason(client: httpx.AsyncClient, session: AsyncSession) -> None:
    await _admin(client, session)
    org, users = await _org(session, owner=MemberRole.OWNER)
    stored = await _file(session, org, users["owner"])

    response = await client.post(
        f"/api/v1/admin/orgs/{org.id}/files/{stored.id}/delete", json={"reason": "spam"}
    )

    assert response.status_code == 422
