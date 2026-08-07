"""
T-263, the HTTP surface: `/api/v1/admin/settings/product`.

Same shape as test_admin_feature_flags_router.py -- superadmin-only, audited,
and using conftest's shared client/session/_redis_client fixtures against real
migrations. What is specific to this endpoint is that it is the surface an
operator reaches for *during an incident*, so the two things asserted hardest
are that a stale cache can never be what they are shown, and that a malformed
value is refused rather than stored.
"""

from typing import Any
from uuid import uuid4

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache, cache_keys
from app.modules.audit.models import AuditEvent
from app.modules.auth.permissions import MemberRole
from app.modules.auth.repository import UserRepository
from app.modules.auth.utils import create_access_token, hash_password
from app.modules.product_settings import keys
from app.modules.product_settings.service import AUDIT_ACTION
from app.modules.tenancy.repository import MembershipRepository, OrganizationRepository

_PASSWORD = "Correct-Horse9"
_URL = "/api/v1/admin/settings/product"


@pytest.fixture
def redis(_redis_client: Redis) -> Redis:
    """Same aliasing as test_admin_impersonation_router.py: the shared
    fixture's leading underscore marks it as setup-only, and this file needs
    to read it."""
    return _redis_client


async def _authenticate(
    client: httpx.AsyncClient, session: AsyncSession, *, is_superadmin: bool
) -> httpx.AsyncClient:
    user = await UserRepository(session).create(
        email=f"user-{uuid4()}@example.com",
        hashed_password=hash_password(_PASSWORD),
        is_superadmin=is_superadmin,
        # T-218: require_superadmin also requires a second factor to be
        # enrolled, so a superadmin fixture without one tests the wrong 403.
        totp_enabled=is_superadmin,
    )
    org = await OrganizationRepository(session).create(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    await MembershipRepository(session).create(
        user_id=user.id, org_id=org.id, role=MemberRole.OWNER
    )
    await session.commit()
    client.headers["Authorization"] = f"Bearer {create_access_token(user.id, None, ())}"
    client.headers["X-Org-ID"] = str(org.id)
    return client


@pytest.fixture
async def admin(client: httpx.AsyncClient, session: AsyncSession) -> httpx.AsyncClient:
    return await _authenticate(client, session, is_superadmin=True)


@pytest.fixture
async def member(client: httpx.AsyncClient, session: AsyncSession) -> httpx.AsyncClient:
    return await _authenticate(client, session, is_superadmin=False)


# ---- authorization -----------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "body"),
    [("GET", None), ("PATCH", {"settings": {}}), ("PUT", {"settings": {}})],
)
async def test_non_superadmin_is_refused(
    member: httpx.AsyncClient, method: str, body: dict[str, Any] | None
) -> None:
    response = await member.request(method, _URL, json=body)

    assert response.status_code == 403


async def test_anonymous_is_refused(client: httpx.AsyncClient) -> None:
    assert (await client.get(_URL)).status_code == 401


# ---- reads -------------------------------------------------------------------


async def test_get_returns_the_stored_settings_and_the_key_registry(
    admin: httpx.AsyncClient,
) -> None:
    response = await admin.get(_URL)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["settings"] == {}
    # The admin UI builds its typed form from this rather than a hardcoded
    # copy, so every well-known key has to arrive with its documentation.
    documented = {entry["key"]: entry for entry in data["well_known"]}
    assert set(documented) == {entry.key for entry in keys.WELL_KNOWN}
    assert documented[keys.MAINTENANCE_MODE]["kind"] == "bool"
    assert documented[keys.MAINTENANCE_MODE]["description"]
    assert data["killable_modules"] == sorted(keys.KILLABLE_MODULE_PREFIXES)


async def test_get_never_serves_a_stale_value(admin: httpx.AsyncClient, redis: Redis) -> None:
    """The read path here is deliberately *not* the 60s cache every other
    reader uses: an operator must not be shown a value up to a minute out of
    date while they decide whether to change it."""
    await admin.patch(_URL, json={"settings": {keys.MAINTENANCE_MODE: True}})
    # Re-warm the cache with the *old* answer, as a concurrent request would.
    await cache.write_json(redis, cache_keys.product_settings_key(), {})

    response = await admin.get(_URL)

    assert response.json()["data"]["settings"][keys.MAINTENANCE_MODE] is True


# ---- writes ------------------------------------------------------------------


async def test_patch_merges_and_returns_the_new_state(admin: httpx.AsyncClient) -> None:
    await admin.patch(_URL, json={"settings": {keys.MAINTENANCE_MESSAGE: "Back at 03:00"}})

    response = await admin.patch(_URL, json={"settings": {keys.MAINTENANCE_MODE: True}})

    assert response.status_code == 200
    assert response.json()["data"]["settings"] == {
        keys.MAINTENANCE_MESSAGE: "Back at 03:00",
        keys.MAINTENANCE_MODE: True,
    }


async def test_patch_with_null_unsets(admin: httpx.AsyncClient) -> None:
    await admin.patch(_URL, json={"settings": {keys.MAINTENANCE_MODE: True}})

    response = await admin.patch(_URL, json={"settings": {keys.MAINTENANCE_MODE: None}})

    assert response.json()["data"]["settings"] == {}


async def test_put_replaces_wholesale(admin: httpx.AsyncClient) -> None:
    await admin.patch(
        _URL, json={"settings": {keys.MAINTENANCE_MODE: True, keys.SIGNUP_DISABLED: True}}
    )

    response = await admin.put(_URL, json={"settings": {keys.SIGNUP_DISABLED: True}})

    assert response.json()["data"]["settings"] == {keys.SIGNUP_DISABLED: True}


async def test_an_unknown_key_is_accepted(admin: httpx.AsyncClient) -> None:
    """What the raw-JSON editor is for: a product built on this template ships
    its own switches without a change to the template's code."""
    response = await admin.patch(_URL, json={"settings": {"acme_holiday_mode": True}})

    assert response.status_code == 200
    assert response.json()["data"]["settings"] == {"acme_holiday_mode": True}


@pytest.mark.parametrize(
    "settings",
    [
        {keys.MAINTENANCE_MODE: "true"},
        {keys.GLOBAL_BANNER: {"type": "urgent", "message": "hi"}},
        {keys.EMERGENCY_KILL_SWITCH: {"admin": True}},
    ],
)
async def test_a_malformed_value_is_422_and_stores_nothing(
    admin: httpx.AsyncClient, settings: dict[str, Any]
) -> None:
    response = await admin.patch(_URL, json={"settings": settings})

    assert response.status_code == 422
    assert response.json()["error"]["message_key"] == "errors.invalid_product_setting"
    assert (await admin.get(_URL)).json()["data"]["settings"] == {}


async def test_the_response_records_who_changed_it_and_when(admin: httpx.AsyncClient) -> None:
    response = await admin.patch(_URL, json={"settings": {keys.MAINTENANCE_MODE: True}})

    data = response.json()["data"]
    assert data["updated_by"] is not None
    assert data["updated_at"] is not None


async def test_every_change_is_audited(admin: httpx.AsyncClient, session: AsyncSession) -> None:
    await admin.patch(_URL, json={"settings": {keys.MAINTENANCE_MODE: True}})

    stmt = select(AuditEvent).where(AuditEvent.action == AUDIT_ACTION)
    events = list((await session.execute(stmt)).scalars().all())

    assert len(events) == 1
    assert events[0].severity == "elevated"
    assert events[0].diff == {keys.MAINTENANCE_MODE: {"from": None, "to": True}}


# ---- the banner reaches /config ----------------------------------------------


async def test_a_banner_set_here_appears_on_config_immediately_and_disappears_on_unset(
    admin: httpx.AsyncClient, client: httpx.AsyncClient
) -> None:
    """T-263's stated cache requirement, from the outside: the write busts the
    60s Redis copy rather than leaving /config a minute behind."""
    banner = {"type": "warning", "message": "Degraded performance", "dismissible": True}

    await admin.patch(_URL, json={"settings": {keys.GLOBAL_BANNER: banner}})
    assert (await client.get("/api/v1/config")).json()["data"]["banner"] == banner

    await admin.patch(_URL, json={"settings": {keys.GLOBAL_BANNER: None}})
    assert (await client.get("/api/v1/config")).json()["data"]["banner"] is None
