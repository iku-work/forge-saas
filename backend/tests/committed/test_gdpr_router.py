"""
Verifies T-033 (GDPR router): the full HTTP surface for data export, the
delete-me flow, and the consent log under /api/v1/me. In particular: the
DELETE /me confirmation gate rejects anything but the exact literal "DELETE"
before any deletion happens, GET /export/{job_id} is ownership-scoped (a job
belonging to another user 404s, not 200s with someone else's data), and
POST /consents is append-only -- two calls produce two rows.

Same real-Postgres + real-Redis testcontainers / httpx.AsyncClient(
ASGITransport(...)) pattern as test_tenancy_router.py -- see that file's
docstring for why TestClient is avoided.
"""

from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from app.modules.auth.models import User
from app.modules.auth.utils import create_access_token

# T-291: this file needs gdpr enabled -- its routes, its tables, or
# both. CI's module-matrix job deselects it in the combination that turns
# every optional module off.
pytestmark = pytest.mark.module_dependent


@pytest.fixture
async def user_a(session: AsyncSession) -> User:
    u = User(email=f"a-{uuid4()}@example.com")
    session.add(u)
    await session.flush()
    await session.commit()
    return u


@pytest.fixture
async def user_b(session: AsyncSession) -> User:
    u = User(email=f"b-{uuid4()}@example.com")
    session.add(u)
    await session.flush()
    await session.commit()
    return u


def _auth_header(user: User) -> dict[str, str]:
    token = create_access_token(user.id, None, ())
    return {"Authorization": f"Bearer {token}"}


# --- GET /me/export ----------------------------------------------------------------


async def test_request_export_requires_auth(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/me/export")

    assert response.status_code == 401


async def test_request_export_happy_path_returns_202_with_job_id(
    client: httpx.AsyncClient, user_a: User
) -> None:
    response = await client.get("/api/v1/me/export", headers=_auth_header(user_a))

    assert response.status_code == 202
    assert "job_id" in response.json()["data"]


# --- GET /me/export/{job_id} -------------------------------------------------------


async def test_get_export_job_happy_path_returns_pending_status(
    client: httpx.AsyncClient, user_a: User
) -> None:
    created = await client.get("/api/v1/me/export", headers=_auth_header(user_a))
    job_id = created.json()["data"]["job_id"]

    response = await client.get(f"/api/v1/me/export/{job_id}", headers=_auth_header(user_a))

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "pending"


async def test_get_export_job_cross_user_returns_404(
    client: httpx.AsyncClient, user_a: User, user_b: User
) -> None:
    created = await client.get("/api/v1/me/export", headers=_auth_header(user_a))
    job_id = created.json()["data"]["job_id"]

    response = await client.get(f"/api/v1/me/export/{job_id}", headers=_auth_header(user_b))

    assert response.status_code == 404


async def test_get_export_job_unknown_id_returns_404(
    client: httpx.AsyncClient, user_a: User
) -> None:
    response = await client.get(f"/api/v1/me/export/{uuid4()}", headers=_auth_header(user_a))

    assert response.status_code == 404


# --- DELETE /me ----------------------------------------------------------------------


async def test_delete_me_requires_auth(client: httpx.AsyncClient) -> None:
    response = await client.request("DELETE", "/api/v1/me", json={"confirm": "DELETE"})

    assert response.status_code == 401


async def test_delete_me_missing_confirm_returns_422(
    client: httpx.AsyncClient, user_a: User
) -> None:
    response = await client.request("DELETE", "/api/v1/me", json={}, headers=_auth_header(user_a))

    assert response.status_code == 422


async def test_delete_me_wrong_confirm_value_returns_422(
    client: httpx.AsyncClient, user_a: User
) -> None:
    response = await client.request(
        "DELETE", "/api/v1/me", json={"confirm": "delete"}, headers=_auth_header(user_a)
    )

    assert response.status_code == 422


async def test_delete_me_happy_path_returns_202_and_soft_deletes_user(
    client: httpx.AsyncClient, user_a: User, session: AsyncSession
) -> None:
    response = await client.request(
        "DELETE", "/api/v1/me", json={"confirm": "DELETE"}, headers=_auth_header(user_a)
    )

    assert response.status_code == 202
    # The request was committed on its own session (see get_session_override);
    # this fixture's session still has the pre-delete row in its identity map
    # (expire_on_commit=False), so a plain re-select would return the stale
    # cached copy instead of hitting the database again.
    await session.refresh(user_a)
    assert user_a.deleted_at is not None


# --- GET /me/consents ----------------------------------------------------------------


async def test_list_consents_requires_auth(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/me/consents")

    assert response.status_code == 401


async def test_list_consents_empty_for_new_user(client: httpx.AsyncClient, user_a: User) -> None:
    response = await client.get("/api/v1/me/consents", headers=_auth_header(user_a))

    assert response.status_code == 200
    assert response.json()["data"] == []


# --- POST /me/consents ---------------------------------------------------------------


async def test_record_consent_missing_fields_returns_422(
    client: httpx.AsyncClient, user_a: User
) -> None:
    response = await client.post(
        "/api/v1/me/consents", json={"granted": True}, headers=_auth_header(user_a)
    )

    assert response.status_code == 422


async def test_record_consent_called_twice_creates_two_rows(
    client: httpx.AsyncClient, user_a: User
) -> None:
    payload = {"consent_type": "marketing", "granted": True, "version": "2026-06-01"}

    first = await client.post("/api/v1/me/consents", json=payload, headers=_auth_header(user_a))
    second = await client.post(
        "/api/v1/me/consents",
        json={**payload, "granted": False},
        headers=_auth_header(user_a),
    )

    assert first.status_code == 200
    assert second.status_code == 200

    listed = await client.get("/api/v1/me/consents", headers=_auth_header(user_a))
    assert len(listed.json()["data"]) == 2
