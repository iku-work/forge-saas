"""Review R5: the shared budgets -- per user across every endpoint, per org.

`@api_limit` counts per endpoint, so a user spreading requests over routes
multiplied their allowance, and nothing bounded an organization's combined
traffic. These tests drive the real dependencies through the real app.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from freezegun import freeze_time
from limits import parse
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter, RateLimiter
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.middleware import rate_limit as rate_limit_module
from app.middleware.budgets import ORG_NAMESPACE
from app.modules.auth.permissions import MemberRole
from app.modules.auth.repository import UserRepository
from app.modules.auth.utils import create_access_token, hash_password
from app.modules.tenancy.models import Organization
from app.modules.tenancy.repository import MembershipRepository, OrganizationRepository

_PASSWORD = "Correct-Horse9"


def _set(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


async def _member(session: AsyncSession, org: Organization, role: MemberRole) -> dict[str, str]:
    user = await UserRepository(session).create(
        email=f"u-{uuid4()}@example.com",
        hashed_password=hash_password(_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    await MembershipRepository(session).create(user_id=user.id, org_id=org.id, role=role)
    await session.commit()
    return {
        "Authorization": f"Bearer {create_access_token(user.id, None, ())}",
        "X-Org-ID": str(org.id),
    }


async def _org(session: AsyncSession) -> Organization:
    org = await OrganizationRepository(session).create(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    await session.commit()
    return org


async def test_spreading_requests_across_endpoints_does_not_multiply_the_user_budget(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Five a minute, alternating two routes: each route's own limit is never
    reached (three calls each), the shared one is."""
    _set(monkeypatch, API_RATE_LIMIT="5/minute")
    headers = await _member(session, await _org(session), MemberRole.OWNER)

    statuses = [
        (await client.get(route, headers=headers)).status_code
        for route in ["/api/v1/auth/me", "/api/v1/orgs"] * 3
    ]

    assert statuses[:5] == [200] * 5
    assert statuses[5] == 429


async def test_the_org_budget_is_shared_by_its_members_and_no_one_else(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set(monkeypatch, API_RATE_LIMIT="1000/minute", ORG_RATE_LIMIT="4/hour")
    org = await _org(session)
    owner = await _member(session, org, MemberRole.OWNER)
    member = await _member(session, org, MemberRole.MEMBER)
    elsewhere = await _member(session, await _org(session), MemberRole.OWNER)
    url = "/api/v1/me/support-context"

    for headers in (owner, member, owner, member):
        assert (await client.get(url, headers=headers)).status_code == 200

    refused = await client.get(url, headers=member)
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) >= 1
    # Another organization is untouched by this one's traffic.
    assert (await client.get(url, headers=elsewhere)).status_code == 200


async def test_path_keyed_org_routes_draw_on_the_same_org_budget(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tenancy names the org in the path, not the header; same budget."""
    _set(monkeypatch, API_RATE_LIMIT="1000/minute", ORG_RATE_LIMIT="2/hour")
    org = await _org(session)
    owner = await _member(session, org, MemberRole.OWNER)

    assert (await client.get(f"/api/v1/orgs/{org.id}", headers=owner)).status_code == 200
    assert (await client.get("/api/v1/me/support-context", headers=owner)).status_code == 200
    assert (await client.get(f"/api/v1/orgs/{org.id}", headers=owner)).status_code == 429


async def test_naming_another_org_spends_nothing_of_its_budget(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Charged after membership is proven. On the header alone, anyone could
    exhaust any organization's budget by sending its id."""
    _set(monkeypatch, API_RATE_LIMIT="1000/minute", ORG_RATE_LIMIT="3/hour")
    victim = await _org(session)
    victim_id = victim.id
    outsider = await _member(session, await _org(session), MemberRole.OWNER)
    outsider["X-Org-ID"] = str(victim_id)

    for _ in range(5):
        response = await client.get("/api/v1/me/support-context", headers=outsider)
        assert response.status_code == 403

    item = parse("3/hour")
    stats = rate_limit_module.current_strategy().get_window_stats(
        item, f"{ORG_NAMESPACE}:{victim_id}"
    )
    assert stats.remaining == 3


def test_the_window_slides() -> None:
    """A fixed window lets a caller spend a whole budget at the end of one
    window and another at the start of the next: twice the rate for a moment.
    The sliding counter weights the previous window in, so just after the
    boundary almost nothing is left. (It rounds the weighted count down, so
    "almost nothing" is one request, not zero.)"""
    item = parse("5/minute")
    start = datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC)

    def allowed_across_the_boundary(strategy: RateLimiter) -> int:
        # One request opens the window; four more arrive just before it
        # closes; then, two seconds later, how many more get through?
        with freeze_time(start) as clock:
            assert strategy.hit(item, "caller")
            clock.move_to(start + timedelta(seconds=59))
            assert all(strategy.hit(item, "caller") for _ in range(4))
            clock.move_to(start + timedelta(seconds=61))
            return sum(strategy.hit(item, "caller") for _ in range(5))

    assert allowed_across_the_boundary(FixedWindowRateLimiter(MemoryStorage())) == 5
    assert allowed_across_the_boundary(rate_limit_module.strategy_for(MemoryStorage())) <= 1
