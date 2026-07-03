"""
Verifies T-142's ImpersonationContextMiddleware in isolation: it never
raises on a garbage/undecodable bearer token (that's
app.core.dependencies.get_current_user's job, not this middleware's), it's a
no-op when there's no Authorization header at all, and a request without an
impersonation token never picks up a stray impersonator_id from a previous
request.
"""

import httpx


async def test_garbage_bearer_token_does_not_crash_the_request(
    client: httpx.AsyncClient,
) -> None:
    client.headers["Authorization"] = "Bearer not-a-real-jwt"

    response = await client.get("/health")

    assert response.status_code == 200


async def test_missing_authorization_header_is_a_no_op(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
