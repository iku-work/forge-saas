"""
T-261: handing a live impersonation session to the superadmin's own phone.

The property that matters here is that the handoff adds a *transport*, not a
second way to become someone else. Everything below is written against that:
the token the phone ends up holding is byte-for-byte the one T-142 already
minted (same jti, same exp, same blocklist entry, same T-260 guard), the
exchange bearer that carries it is single-use and two minutes old at most, and
the two unauthenticated legs still land in the audit trail stamped with the
superadmin behind the session -- even though neither request carries a token
for app.middleware.impersonation to read.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
import pytest
from freezegun import freeze_time
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.admin.impersonation_handoff import (
    HANDOFF_TTL_SECONDS,
    HandoffRecord,
    generate_exchange_token,
    handoff_expiry,
    store_handoff,
    take_handoff,
)
from app.modules.audit.models import AuditEvent
from app.modules.auth.models import User
from app.modules.auth.repository import UserRepository
from app.modules.auth.utils import hash_password, verify_access_token
from tests._auth import session_token

# T-291: this file needs admin enabled -- its routes, its tables, or
# both. CI's module-matrix job deselects it in the combination that turns
# every optional module off.
pytestmark = pytest.mark.module_dependent

_PASSWORD = "Correct-Horse9"
_REASON = "User reports the upload sheet crashes on iOS, ticket 8812"


async def _make_user(
    session: AsyncSession, *, is_superadmin: bool = False, totp_enabled: bool = False
) -> User:
    user = await UserRepository(session).create(
        email=f"user-{uuid4()}@example.com",
        hashed_password=hash_password(_PASSWORD),
        email_verified_at=datetime.now(UTC),
        is_superadmin=is_superadmin,
        totp_enabled=totp_enabled,
    )
    await session.commit()
    return user


async def _start_impersonation(
    client: httpx.AsyncClient, session: AsyncSession, *, reason: str = _REASON
) -> tuple[str, User, User]:
    """Runs the real T-142 flow and returns (impersonation token, target, admin).

    Deliberately not a hand-minted token: the reason the phone displays is
    written by `start()`, so a test that skipped it would be testing a handoff
    of a session that never existed.
    """
    admin = await _make_user(session, is_superadmin=True, totp_enabled=True)
    target = await _make_user(session)
    client.headers["Authorization"] = f"Bearer {await session_token(session, admin)}"
    response = await client.post(
        f"/api/v1/admin/users/{target.id}/impersonate",
        json={"reason": reason, "duration_minutes": 30},
    )
    assert response.status_code == 200
    token: str = response.json()["data"]["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return token, target, admin


async def _handoff(client: httpx.AsyncClient) -> tuple[str, str]:
    """Creates a handoff from whatever session `client` currently holds and
    returns (exchange token, deep link)."""
    response = await client.post("/api/v1/admin/impersonate/mobile-handoff")
    assert response.status_code == 200, response.text
    deep_link: str = response.json()["data"]["deep_link"]
    query = parse_qs(urlparse(deep_link).query)
    return query["token"][0], deep_link


async def _preview(client: httpx.AsyncClient, exchange_token: str) -> httpx.Response:
    return await client.post(
        "/api/v1/admin/impersonate/mobile-handoff/preview", json={"token": exchange_token}
    )


async def _events(session: AsyncSession, action: str) -> list[AuditEvent]:
    result = await session.execute(select(AuditEvent).where(AuditEvent.action == action))
    return list(result.scalars().all())


# ---- POST /impersonate/mobile-handoff ----------------------------------------


async def test_handoff_requires_an_active_impersonation_session(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    # A superadmin's own ordinary token is authenticated but is not an
    # impersonation session -- there is nothing to hand off.
    admin = await _make_user(session, is_superadmin=True, totp_enabled=True)
    client.headers["Authorization"] = f"Bearer {await session_token(session, admin)}"

    response = await client.post("/api/v1/admin/impersonate/mobile-handoff")

    assert response.status_code == 400
    assert response.json()["error"]["message_key"] == "errors.not_impersonating"


async def test_handoff_without_a_token_returns_401(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/admin/impersonate/mobile-handoff")

    assert response.status_code == 401


async def test_handoff_returns_a_deep_link_carrying_the_token_and_the_target_email(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, target, _ = await _start_impersonation(client, session)

    response = await client.post("/api/v1/admin/impersonate/mobile-handoff")

    assert response.status_code == 200
    data = response.json()["data"]
    parsed = urlparse(data["deep_link"])
    assert parsed.scheme == "forge"
    assert parsed.netloc == "impersonate"
    query = parse_qs(parsed.query)
    assert query["user_email"] == [target.email]
    # Length, not the exact value: "not a stub" is the assertion; how many
    # bytes secrets.token_urlsafe spends is its own business.
    assert len(query["token"][0]) > 20
    expires_at = datetime.fromisoformat(data["expires_at"])
    assert (
        timedelta(seconds=0)
        < expires_at - datetime.now(UTC)
        <= timedelta(seconds=HANDOFF_TTL_SECONDS)
    )


async def test_handoff_writes_an_elevated_audit_event_stamped_with_the_impersonator(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, target, admin = await _start_impersonation(client, session)

    await _handoff(client)

    events = await _events(session, "impersonation.mobile_handoff_created")
    assert len(events) == 1
    assert events[0].severity == "elevated"
    assert events[0].actor_id == target.id
    assert events[0].impersonator_id == admin.id


async def test_exchange_token_is_never_stored_in_the_clear(
    client: httpx.AsyncClient, session: AsyncSession, redis: Redis
) -> None:
    await _start_impersonation(client, session)

    exchange_token, _ = await _handoff(client)

    keys = [key.decode() for key in await redis.keys("impersonation:handoff:*")]
    assert len(keys) == 1
    assert exchange_token not in keys[0]


# ---- GET /impersonate/mobile-handoff/{token} (preview) ------------------------


async def test_preview_shows_the_reason_from_the_original_impersonation_request(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, target, _ = await _start_impersonation(client, session)
    exchange_token, _ = await _handoff(client)
    del client.headers["Authorization"]  # the phone has no session yet

    response = await client.post(
        "/api/v1/admin/impersonate/mobile-handoff/preview", json={"token": exchange_token}
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["user_email"] == target.email
    assert data["reason"] == _REASON
    # 30 minutes were requested; what matters is that it is the *session's*
    # deadline, not the two-minute exchange window.
    remaining = datetime.fromisoformat(data["session_expires_at"]) - datetime.now(UTC)
    assert timedelta(minutes=25) < remaining <= timedelta(minutes=30)


async def test_preview_does_not_consume_the_token(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _start_impersonation(client, session)
    exchange_token, _ = await _handoff(client)

    first = await _preview(client, exchange_token)
    second = await _preview(client, exchange_token)
    exchange = await client.post(
        "/api/v1/admin/impersonate/mobile-exchange", json={"token": exchange_token}
    )

    assert (first.status_code, second.status_code, exchange.status_code) == (200, 200, 200)


async def test_preview_of_an_unknown_token_returns_401(client: httpx.AsyncClient) -> None:
    response = await _preview(client, "not-a-real-token")

    assert response.status_code == 401


async def test_preview_never_puts_the_exchange_token_in_the_url(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """The token is a live credential for two minutes, and a URL is the one
    place a request leaks by default: proxy access logs, and Sentry's
    `request.url`, which app.core.sentry's _before_send does not scrub (it
    covers extra/contexts/data/headers/cookies). This is the regression test
    for that -- it is about where the secret travels, not about the verb."""
    await _start_impersonation(client, session)
    exchange_token, _ = await _handoff(client)

    response = await _preview(client, exchange_token)

    assert response.status_code == 200
    assert exchange_token not in str(response.request.url)


# ---- POST /impersonate/mobile-exchange ---------------------------------------


async def test_exchange_returns_the_session_token_that_was_already_minted(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    impersonation_token, target, admin = await _start_impersonation(client, session)
    exchange_token, _ = await _handoff(client)
    del client.headers["Authorization"]

    response = await client.post(
        "/api/v1/admin/impersonate/mobile-exchange", json={"token": exchange_token}
    )

    assert response.status_code == 200
    data = response.json()["data"]
    # Byte-for-byte the same token, which is the whole design: one session,
    # one jti, one blocklist entry, one expiry -- carried to a second device.
    assert data["access_token"] == impersonation_token
    assert data["user_email"] == target.email
    assert data["reason"] == _REASON
    payload = verify_access_token(data["access_token"])
    assert payload.user_id == target.id
    assert payload.impersonator_id == admin.id


async def test_exchange_token_is_single_use(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _start_impersonation(client, session)
    exchange_token, _ = await _handoff(client)

    first = await client.post(
        "/api/v1/admin/impersonate/mobile-exchange", json={"token": exchange_token}
    )
    second = await client.post(
        "/api/v1/admin/impersonate/mobile-exchange", json={"token": exchange_token}
    )

    assert first.status_code == 200
    assert second.status_code == 401
    assert second.json()["error"]["message_key"] == "errors.invalid_exchange_token"


async def test_exchange_token_expires_after_two_minutes(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _start_impersonation(client, session)
    exchange_token, _ = await _handoff(client)

    # Redis' own TTL would evict the key eventually, but only in wall-clock
    # time -- the record's absolute `expires_at`, checked in Python, is what
    # makes the deadline the rule rather than a cleanup detail, and it is what
    # this can advance onto.
    with freeze_time(datetime.now(UTC) + timedelta(seconds=HANDOFF_TTL_SECONDS + 1)):
        response = await client.post(
            "/api/v1/admin/impersonate/mobile-exchange", json={"token": exchange_token}
        )

    assert response.status_code == 401


async def test_exchange_writes_an_audit_event_stamped_with_the_impersonator(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, target, admin = await _start_impersonation(client, session)
    exchange_token, _ = await _handoff(client)
    del client.headers["Authorization"]

    await client.post("/api/v1/admin/impersonate/mobile-exchange", json={"token": exchange_token})

    events = await _events(session, "impersonation.mobile_handoff")
    assert len(events) == 1
    assert events[0].severity == "elevated"
    assert events[0].actor_id == target.id
    # The request carried no bearer token at all, so the middleware set
    # nothing -- the service restores it from the stored record instead.
    assert events[0].impersonator_id == admin.id


async def test_exchanged_token_is_still_refused_by_the_t260_blocklist(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _start_impersonation(client, session)
    exchange_token, _ = await _handoff(client)
    del client.headers["Authorization"]
    exchange = await client.post(
        "/api/v1/admin/impersonate/mobile-exchange", json={"token": exchange_token}
    )
    client.headers["Authorization"] = f"Bearer {exchange.json()['data']['access_token']}"

    # A destructive action, attempted from the phone this time.
    response = await client.post("/api/v1/auth/change-password", json={})

    assert response.status_code == 403
    assert response.json()["error"]["message_key"] == "errors.impersonation_blocked"


async def test_a_handoff_of_an_ended_session_cannot_be_exchanged_into_a_working_token(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _start_impersonation(client, session)
    exchange_token, _ = await _handoff(client)
    await client.post("/api/v1/admin/impersonate/end")

    exchange = await client.post(
        "/api/v1/admin/impersonate/mobile-exchange", json={"token": exchange_token}
    )
    # The exchange itself still succeeds -- the handoff record knows nothing
    # about the blocklist -- but what comes out is the ended session's jti,
    # which get_current_user refuses. Ending is the authority; the two-minute
    # window is not a hole in it.
    assert exchange.status_code == 200
    client.headers["Authorization"] = f"Bearer {exchange.json()['data']['access_token']}"

    assert (await client.get("/api/v1/auth/me")).status_code == 401


# ---- POST /impersonate/mobile-cancel -----------------------------------------


async def test_cancel_invalidates_the_token_immediately(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _start_impersonation(client, session)
    exchange_token, _ = await _handoff(client)
    del client.headers["Authorization"]

    cancel = await client.post(
        "/api/v1/admin/impersonate/mobile-cancel", json={"token": exchange_token}
    )
    exchange = await client.post(
        "/api/v1/admin/impersonate/mobile-exchange", json={"token": exchange_token}
    )

    assert cancel.status_code == 200
    assert exchange.status_code == 401


async def test_cancel_of_an_unknown_token_succeeds_silently(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    response = await client.post(
        "/api/v1/admin/impersonate/mobile-cancel", json={"token": "never-existed"}
    )

    assert response.status_code == 200
    # No event: the endpoint is unauthenticated, so writing one per unknown
    # token would let anyone fill the feed, and there is nothing to record.
    assert await _events(session, "impersonation.mobile_handoff_cancelled") == []


async def test_a_handoff_can_only_be_taken_once_even_by_concurrent_callers(
    redis: Redis,
) -> None:
    """The primitive behind both the exchange and the cancel leg.

    Two devices opening the same link is the ordinary case, not the exotic one:
    an operator scans, the OS delivers the link to a second handler, or Cancel
    is tapped just as Confirm lands. Both legs consume the record through this
    one GETDEL, so whichever arrives second finds nothing -- which is what
    stops the audit trail from recording that a session was declined and
    adopted.

    Tested here rather than through the two endpoints because the test client
    shares a single AsyncSession (and so a single asyncpg connection) across
    requests, which cannot serve two of them at once -- the concurrency this
    is about lives in Redis, not in the request handlers.
    """
    token = generate_exchange_token()
    await store_handoff(
        redis,
        token,
        HandoffRecord(
            access_token="jwt",
            user_id=uuid4(),
            user_email="jane@example.com",
            impersonator_id=uuid4(),
            reason=_REASON,
            session_expires_at=datetime.now(UTC) + timedelta(minutes=30),
            expires_at=handoff_expiry(),
        ),
    )

    taken = await asyncio.gather(take_handoff(redis, token), take_handoff(redis, token))

    assert sum(record is not None for record in taken) == 1


async def test_cancel_writes_an_audit_event_for_a_real_handoff(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, target, admin = await _start_impersonation(client, session)
    exchange_token, _ = await _handoff(client)
    del client.headers["Authorization"]

    await client.post("/api/v1/admin/impersonate/mobile-cancel", json={"token": exchange_token})

    events = await _events(session, "impersonation.mobile_handoff_cancelled")
    assert len(events) == 1
    assert events[0].actor_id == target.id
    assert events[0].impersonator_id == admin.id


# ---- POST /impersonate/end, channel=mobile -----------------------------------


async def test_ending_from_mobile_writes_its_own_audit_action(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, target, admin = await _start_impersonation(client, session)

    response = await client.post("/api/v1/admin/impersonate/end", json={"channel": "mobile"})

    assert response.status_code == 200
    events = await _events(session, "impersonation.mobile_ended")
    assert len(events) == 1
    assert events[0].severity == "elevated"
    assert events[0].actor_id == target.id
    assert events[0].impersonator_id == admin.id
    # ...and not under the web action, or the feed would double-count.
    assert await _events(session, "admin.impersonate.end") == []


async def test_ending_without_a_body_still_records_the_web_action(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _start_impersonation(client, session)

    response = await client.post("/api/v1/admin/impersonate/end")

    assert response.status_code == 200
    assert len(await _events(session, "admin.impersonate.end")) == 1
    assert await _events(session, "impersonation.mobile_ended") == []


async def test_ending_from_mobile_revokes_the_session_everywhere(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    # One session, two devices: ending it on the phone has to end it on the
    # laptop too, since both hold the same jti.
    impersonation_token, _, _ = await _start_impersonation(client, session)
    exchange_token, _ = await _handoff(client)
    await client.post("/api/v1/admin/impersonate/mobile-exchange", json={"token": exchange_token})

    await client.post("/api/v1/admin/impersonate/end", json={"channel": "mobile"})

    client.headers["Authorization"] = f"Bearer {impersonation_token}"
    assert (await client.get("/api/v1/auth/me")).status_code == 401
