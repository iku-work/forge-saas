"""Feedback: a signed-in user sends a message from the app; superadmins are
told, read it in the admin Feedback inbox, and mark it handled."""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import User
from app.modules.auth.permissions import MemberRole
from app.modules.auth.repository import UserRepository
from app.modules.auth.utils import hash_password
from app.modules.feedback.models import Feedback
from app.modules.feedback.repository import FeedbackRepository
from app.modules.notifications.models import Notification
from app.modules.tenancy.models import Membership, Organization
from tests._auth import session_token

# The inbox is part of the admin module; the alert goes through notifications.
pytestmark = pytest.mark.module_dependent


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


async def _org_with(session: AsyncSession, user: User) -> Organization:
    org = Organization(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    session.add(org)
    await session.flush()
    session.add(Membership(user_id=user.id, org_id=org.id, role=MemberRole.MEMBER))
    await session.commit()
    return org


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _send(client: httpx.AsyncClient, token: str, **body: Any) -> httpx.Response:  # noqa: ANN401 -- the JSON body
    payload = {"category": "bug", "message": "Upload button does nothing", **body}
    return await client.post("/api/v1/feedback", json=payload, headers=_auth(token))


async def _feedback_rows(session: AsyncSession, user_id: UUID) -> list[Feedback]:
    session.expire_all()
    stmt = select(Feedback).where(cast("Any", Feedback).user_id == user_id)
    return list((await session.execute(stmt)).scalars().all())


# --- sending ------------------------------------------------------------------


async def test_feedback_is_saved_with_where_it_came_from_and_admins_are_told(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin = await _user(session, is_superadmin=True, totp_enabled=True)
    sender = await _user(session)
    org = await _org_with(session, sender)
    admin_id, sender_id, org_id = admin.id, sender.id, org.id

    response = await _send(
        client, await session_token(session, sender), org_id=str(org_id), page="/files"
    )

    assert response.status_code == 200
    [row] = await _feedback_rows(session, sender_id)
    assert (row.category, row.page, row.org_id) == ("bug", "/files", org_id)
    m = cast("Any", Notification)
    told = (
        (await session.execute(select(m.user_id).where(m.type == "feedback_received")))
        .scalars()
        .all()
    )
    assert admin_id in told
    assert sender_id not in told


async def test_an_org_that_is_not_yours_is_refused(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    sender = await _user(session)
    sender_id = sender.id
    other_org = await _org_with(session, await _user(session))

    response = await _send(client, await session_token(session, sender), org_id=str(other_org.id))

    assert response.status_code == 403
    assert await _feedback_rows(session, sender_id) == []


@pytest.mark.parametrize(
    "body",
    [
        {"message": ""},
        {"message": "x" * 5001},
        {"category": "complaint"},
        # A path only: a query string could carry a token.
        {"page": "/reset-password?token=secret"},
        {"page": "https://example.com/files"},
    ],
)
async def test_bad_input_is_refused(
    client: httpx.AsyncClient, session: AsyncSession, body: dict[str, str]
) -> None:
    sender = await _user(session)

    response = await _send(client, await session_token(session, sender), **body)

    assert response.status_code == 422


async def test_you_have_to_be_signed_in(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/feedback", json={"category": "bug", "message": "Hi"})

    assert response.status_code == 401


async def test_sending_is_rate_limited(client: httpx.AsyncClient, session: AsyncSession) -> None:
    token = await session_token(session, await _user(session))

    codes = [(await _send(client, token)).status_code for _ in range(4)]

    assert codes == [200, 200, 200, 429]


# --- the inbox ----------------------------------------------------------------


async def test_the_inbox_lists_open_feedback_and_moves_it_when_handled(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin = await _user(session, is_superadmin=True, totp_enabled=True)
    sender = await _user(session)
    org = await _org_with(session, sender)
    sender_email = sender.email
    await _send(client, await session_token(session, sender), org_id=str(org.id), page="/files")
    client.headers.update(_auth(await session_token(session, admin)))

    [item] = (await client.get("/api/v1/admin/feedback")).json()["data"]
    assert (item["user_email"], item["org_name"], item["page"]) == (sender_email, "Acme", "/files")
    assert item["resolved_at"] is None

    resolved = await client.post(f"/api/v1/admin/feedback/{item['id']}/resolve")
    assert resolved.status_code == 200
    assert resolved.json()["data"]["resolved_at"] is not None
    assert (await client.get("/api/v1/admin/feedback")).json()["data"] == []
    handled = (await client.get("/api/v1/admin/feedback?status=resolved")).json()["data"]
    assert [i["id"] for i in handled] == [item["id"]]

    reopened = await client.post(f"/api/v1/admin/feedback/{item['id']}/reopen")
    assert reopened.json()["data"]["resolved_at"] is None
    assert len((await client.get("/api/v1/admin/feedback")).json()["data"]) == 1


async def test_handling_unknown_feedback_is_a_404(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    admin = await _user(session, is_superadmin=True, totp_enabled=True)
    client.headers.update(_auth(await session_token(session, admin)))

    response = await client.post(f"/api/v1/admin/feedback/{uuid4()}/resolve")

    assert response.status_code == 404


async def test_only_a_superadmin_reads_the_inbox(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    client.headers.update(_auth(await session_token(session, await _user(session))))

    response = await client.get("/api/v1/admin/feedback")

    assert response.status_code == 403


async def test_the_inbox_pages_by_cursor(client: httpx.AsyncClient, session: AsyncSession) -> None:
    admin = await _user(session, is_superadmin=True, totp_enabled=True)
    for _ in range(3):
        sender = await _user(session)
        await FeedbackRepository(session).create(
            user_id=sender.id, org_id=None, category="idea", message="More", page=None
        )
    await session.commit()
    client.headers.update(_auth(await session_token(session, admin)))

    first = (await client.get("/api/v1/admin/feedback?limit=2")).json()
    cursor = first["meta"]["next_cursor"]
    second = (await client.get(f"/api/v1/admin/feedback?limit=2&after={cursor}")).json()
    stale = await client.get(f"/api/v1/admin/feedback?after={uuid4()}")

    ids = [i["id"] for i in first["data"] + second["data"]]
    assert len(ids) == len(set(ids)) == 3
    assert second["meta"]["next_cursor"] is None
    assert stale.status_code == 422
