"""
Verifies T-048 (backend test infrastructure): the shared engine/session/
client/authed_client fixtures in conftest.py work end-to-end against real
Postgres + Redis testcontainers, with the schema produced by the actual
Alembic migrations rather than a hand-picked table subset.
"""

import httpx


async def test_authed_client_can_fetch_me(authed_client: httpx.AsyncClient) -> None:
    response = await authed_client.get("/api/v1/auth/me")

    assert response.status_code == 200
    assert response.json()["data"]["email"] == "owner@example.com"
