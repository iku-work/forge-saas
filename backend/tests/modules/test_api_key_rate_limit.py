"""
Per-key rate limiting (T-312).

Everything here goes through a real request against `app.main`, because what
is being asserted is not "the limiter counts" -- `limits` can be trusted for
that -- but the four things around it that are this repo's own:

  * the budget is per **key**, so a second key in the same organization is
    untouched by the first one being refused;
  * a **session** on the same endpoint is limited by the user limiter, not
    this one, which falls out of the two keyspaces being disjoint;
  * the per-key **override** takes effect without a restart;
  * the budget is **read at request time**, never bound at import -- the trap
    that bit T-223's rate limiter and `test_middleware.py` both.

`backend/.env.test` sets `API_KEY_RATE_LIMIT_PER_MINUTE=5`, small enough that
a test can exhaust it in six requests.
"""

from collections.abc import Generator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from limits.storage import RedisStorage
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlmodel import SQLModel

from app.core import module_loader
from app.core.config import get_settings
from app.core.modules import ModuleConfig
from app.modules.api_keys.models import ApiKey
from app.modules.api_keys.schemas import ApiKeyCreateRequest, ApiKeyUpdateRequest
from app.modules.api_keys.service import ApiKeyService
from app.modules.auth.permissions import MemberRole
from app.modules.auth.repository import UserRepository
from app.modules.auth.utils import create_access_token, hash_password
from app.modules.tenancy.models import Organization
from app.modules.tenancy.repository import MembershipRepository, OrganizationRepository
from product import config as product_config

_PASSWORD = "Correct-Horse9"
_SCOPES = ["members:read"]


@pytest.fixture(autouse=True)
async def _api_key_table(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all, tables=[ApiKey.__table__])


@pytest.fixture(autouse=True)
def public_api_enabled(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    monkeypatch.setattr(
        product_config,
        "MODULES",
        replace(product_config.MODULES, public_api=ModuleConfig(enabled=True)),
    )
    module_loader.get_modules.cache_clear()
    yield
    module_loader.get_modules.cache_clear()


@dataclass
class LimitWorld:
    client: httpx.AsyncClient
    session: AsyncSession
    redis: Redis
    org: Organization
    owner_id: UUID
    url: str


@pytest.fixture
def redis(_redis_client: Redis) -> Redis:
    return _redis_client


@pytest.fixture
async def world(
    client: httpx.AsyncClient,
    session: AsyncSession,
    redis: Redis,
    _rate_limit_storage: RedisStorage,
) -> LimitWorld:
    # A shared window across tests would make "the sixth request is refused"
    # depend on execution order.
    _rate_limit_storage.reset()
    user = await UserRepository(session).create(
        email=f"user-{uuid4()}@example.com",
        hashed_password=hash_password(_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    org = await OrganizationRepository(session).create(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    await MembershipRepository(session).create(
        user_id=user.id, org_id=org.id, role=MemberRole.OWNER
    )
    await session.commit()
    return LimitWorld(
        client=client,
        session=session,
        redis=redis,
        org=org,
        owner_id=user.id,
        url=f"/api/v1/orgs/{org.id}/members",
    )


async def _mint(world: LimitWorld, name: str = "integration") -> tuple[UUID, dict[str, str]]:
    service = ApiKeyService(world.session, world.org.id, world.redis)
    key, presented = await service.create_key(
        world.owner_id, ApiKeyCreateRequest(name=name, scopes=_SCOPES)
    )
    await world.session.commit()
    return key.id, {"Authorization": f"Bearer {presented}", "X-Org-ID": str(world.org.id)}


def _budget() -> int:
    return get_settings().API_KEY_RATE_LIMIT_PER_MINUTE


# --- the limit itself --------------------------------------------------------


async def test_the_budget_plus_one_is_refused_with_the_documented_headers(
    world: LimitWorld,
) -> None:
    _, headers = await _mint(world)
    budget = _budget()

    for index in range(budget):
        allowed = await world.client.get(world.url, headers=headers)
        assert allowed.status_code == 200, f"request {index + 1} of {budget} was refused"
        assert allowed.headers["X-RateLimit-Limit"] == str(budget)
        # Present on the *success* path too: a client that can see it is at
        # 3 of 5 can slow down before it is refused.
        assert int(allowed.headers["X-RateLimit-Remaining"]) == budget - index - 1
        assert int(allowed.headers["X-RateLimit-Reset"]) >= 1

    refused = await world.client.get(world.url, headers=headers)

    assert refused.status_code == 429
    assert refused.json()["error"]["code"] == "rate_limited"
    assert refused.headers["X-RateLimit-Limit"] == str(budget)
    assert refused.headers["X-RateLimit-Remaining"] == "0"
    # RFC 9110 §10.2.3, in seconds rather than as an HTTP-date.
    assert int(refused.headers["Retry-After"]) >= 1


async def test_a_second_key_in_the_same_org_is_unaffected(world: LimitWorld) -> None:
    """Per key, not per organization and not per IP. Two integrations behind
    one company must not be able to starve each other."""
    _, first = await _mint(world, "first")
    _, second = await _mint(world, "second")

    for _ in range(_budget()):
        assert (await world.client.get(world.url, headers=first)).status_code == 200
    assert (await world.client.get(world.url, headers=first)).status_code == 429

    assert (await world.client.get(world.url, headers=second)).status_code == 200


async def test_a_session_on_the_same_endpoint_is_not_limited_by_the_key_budget(
    world: LimitWorld,
) -> None:
    """The keyspaces are disjoint (`api_key:<id>` vs `@api_limit`'s
    `user:<id>`), so exhausting a key's budget cannot lock a person out of the
    endpoint the key was hitting -- which would otherwise be a self-inflicted
    outage every time an integration misbehaved."""
    _, key_headers = await _mint(world)
    for _ in range(_budget()):
        await world.client.get(world.url, headers=key_headers)
    assert (await world.client.get(world.url, headers=key_headers)).status_code == 429

    session_headers = {
        "Authorization": f"Bearer {create_access_token(world.owner_id, None, ())}",
        "X-Org-ID": str(world.org.id),
    }
    assert (await world.client.get(world.url, headers=session_headers)).status_code == 200


async def test_a_per_key_override_takes_effect_without_a_restart(world: LimitWorld) -> None:
    """The column exists so one integration can be raised without raising
    everyone. It is read off the cached row, so the write has to bust that
    entry -- this fails if `update_key` ever stops doing so."""
    key_id, headers = await _mint(world)
    raised = _budget() + 3

    await ApiKeyService(world.session, world.org.id, world.redis).update_key(
        world.owner_id, key_id, ApiKeyUpdateRequest(rate_limit_per_minute=raised)
    )
    await world.session.commit()

    for index in range(raised):
        response = await world.client.get(world.url, headers=headers)
        assert response.status_code == 200, f"request {index + 1} of {raised} was refused"
        assert response.headers["X-RateLimit-Limit"] == str(raised)

    assert (await world.client.get(world.url, headers=headers)).status_code == 429


async def test_the_budget_is_read_at_request_time(
    world: LimitWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The import-time-binding regression test T-312 asks for by name.

    If the budget were read once, at import, this would keep the value
    `backend/.env` happened to hold when `app.main` loaded -- which is the
    exact bug that made "the Nth call gets 429" false on a developer machine
    for T-223's limiter (see app/middleware/rate_limit.py's comment). Changing
    the setting mid-test and seeing the very next request obey it is what
    proves it is read through `get_settings()` each time.
    """
    _, headers = await _mint(world)
    settings = get_settings()
    monkeypatch.setattr(settings, "API_KEY_RATE_LIMIT_PER_MINUTE", 1)

    first = await world.client.get(world.url, headers=headers)
    assert first.status_code == 200
    assert first.headers["X-RateLimit-Limit"] == "1"

    assert (await world.client.get(world.url, headers=headers)).status_code == 429


async def test_a_refused_request_does_not_reach_the_endpoint(world: LimitWorld) -> None:
    """The limit is consumed in the auth dependency, so a 429 costs one Redis
    round trip rather than a page of members. A limiter that only wrapped the
    response would have protected nothing."""
    _, headers = await _mint(world)
    for _ in range(_budget()):
        await world.client.get(world.url, headers=headers)

    refused = await world.client.get(world.url, headers=headers)

    assert refused.status_code == 429
    assert refused.json()["data"] is None
