"""
Verifies T-120's FeatureFlagService: the two-layer resolution (Plan.features
AND OrgFeatureFlag override, override can only narrow -- never grant --
access), the 60s Redis cache on both is_enabled and all_enabled (including
that a cache hit skips the database entirely), invalidate() clearing every
per-flag key plus the `:all` key for an org, and that set_override/
delete_override invalidate the cache themselves so a caller never has to
remember to.

Same real-Postgres + real-Redis testcontainers pattern as
test_notifications_repository.py / test_storage_service.py, via conftest's
shared session/redis_client/engine fixtures -- Plan, Subscription,
Organization, and OrgFeatureFlag all already have real migrations.
"""

from collections.abc import Generator
from datetime import timedelta
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core import module_loader
from app.core.modules import EnabledModules, ModuleConfig
from app.models._mixins import utcnow
from app.modules.billing.models import Plan, Subscription
from app.modules.feature_flags.repository import OrgFeatureFlagRepository
from app.modules.feature_flags.service import FeatureFlagService
from app.modules.tenancy.models import Organization
from app.modules.tenancy.repository import OrganizationRepository
from product import config as product_config

# T-291: this file needs feature_flags + billing enabled -- its routes, its tables, or
# both. CI's module-matrix job deselects it in the combination that turns
# every optional module off.
pytestmark = pytest.mark.module_dependent

# Same dependents-must-go-too caveat as test_billing_dependencies.py's
# _BILLING_OFF: notifications/file_storage both `requires=["billing"]`.
_BILLING_OFF = EnabledModules(
    billing=ModuleConfig(enabled=False),
    notifications=ModuleConfig(enabled=False),
    file_storage=ModuleConfig(enabled=False),
)


@pytest.fixture(autouse=True)
def _clear_modules_cache() -> Generator[None]:
    module_loader.get_modules.cache_clear()
    yield
    module_loader.get_modules.cache_clear()


@pytest.fixture
async def org(session: AsyncSession) -> Organization:
    return await OrganizationRepository(session).create(name="Acme", slug=f"acme-{uuid4().hex[:8]}")


async def _make_active_plan_and_subscription(
    session: AsyncSession, org: Organization, features: dict[str, int]
) -> Plan:
    plan = Plan(
        name="Pro",
        slug=f"pro-{uuid4().hex[:8]}",
        stripe_price_id_monthly="price_m",
        stripe_price_id_yearly="price_y",
        price_monthly=2900,
        price_yearly=29000,
        features=features,
    )
    session.add(plan)
    await session.flush()
    subscription = Subscription(
        org_id=org.id,
        plan_id=plan.id,
        stripe_subscription_id=f"sub_{uuid4().hex[:8]}",
        status="active",
        current_period_start=utcnow() - timedelta(days=10),
        current_period_end=utcnow() + timedelta(days=20),
    )
    session.add(subscription)
    await session.flush()
    return plan


# --- is_enabled: resolution paths ------------------------------------------------------


async def test_is_enabled_true_when_plan_grants_and_no_override(
    session: AsyncSession, redis: Redis, org: Organization
) -> None:
    await _make_active_plan_and_subscription(session, org, {"advanced_reports": 1})

    result = await FeatureFlagService(session, redis).is_enabled(org, "advanced_reports")

    assert result is True


async def test_is_enabled_false_when_override_disables_a_plan_granted_flag(
    session: AsyncSession, redis: Redis, org: Organization
) -> None:
    await _make_active_plan_and_subscription(session, org, {"advanced_reports": 1})
    await OrgFeatureFlagRepository(session, org.id).set_override("advanced_reports", enabled=False)

    result = await FeatureFlagService(session, redis).is_enabled(org, "advanced_reports")

    assert result is False


async def test_is_enabled_false_when_absent_from_plan_even_with_override_true(
    session: AsyncSession, redis: Redis, org: Organization
) -> None:
    await _make_active_plan_and_subscription(session, org, {})
    await OrgFeatureFlagRepository(session, org.id).set_override("advanced_reports", enabled=True)

    result = await FeatureFlagService(session, redis).is_enabled(org, "advanced_reports")

    assert result is False


async def test_is_enabled_false_on_free_plan_with_no_subscription(
    session: AsyncSession, redis: Redis, org: Organization
) -> None:
    result = await FeatureFlagService(session, redis).is_enabled(org, "advanced_reports")

    assert result is False


async def test_is_enabled_cache_hit_skips_the_database(
    session: AsyncSession, redis: Redis, org: Organization, engine: AsyncEngine
) -> None:
    await _make_active_plan_and_subscription(session, org, {"advanced_reports": 1})
    service = FeatureFlagService(session, redis)
    await service.is_enabled(org, "advanced_reports")  # populates the cache

    query_count = 0

    def _count_queries(*_args: object, **_kwargs: object) -> None:
        nonlocal query_count
        query_count += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _count_queries)
    try:
        cached = await service.is_enabled(org, "advanced_reports")
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _count_queries)

    assert cached is True
    assert query_count == 0


async def test_is_enabled_when_billing_disabled_defaults_true(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession, redis: Redis, org: Organization
) -> None:
    monkeypatch.setattr(product_config, "MODULES", _BILLING_OFF)
    module_loader.get_modules.cache_clear()

    result = await FeatureFlagService(session, redis).is_enabled(org, "beta_ui")

    assert result is True


async def test_is_enabled_when_billing_disabled_override_still_wins(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession, redis: Redis, org: Organization
) -> None:
    monkeypatch.setattr(product_config, "MODULES", _BILLING_OFF)
    module_loader.get_modules.cache_clear()
    await OrgFeatureFlagRepository(session, org.id).set_override("beta_ui", enabled=False)

    result = await FeatureFlagService(session, redis).is_enabled(org, "beta_ui")

    assert result is False


# --- all_enabled -------------------------------------------------------------------


async def test_all_enabled_resolves_plan_and_override_map(
    session: AsyncSession, redis: Redis, org: Organization
) -> None:
    await _make_active_plan_and_subscription(
        session, org, {"advanced_reports": 1, "beta_ui": 1, "off_by_plan": 0}
    )
    await OrgFeatureFlagRepository(session, org.id).set_override("beta_ui", enabled=False)
    await OrgFeatureFlagRepository(session, org.id).set_override("admin_only", enabled=True)

    result = await FeatureFlagService(session, redis).all_enabled(org)

    assert result == {
        "advanced_reports": True,
        "beta_ui": False,  # override wins over the plan's True
        "off_by_plan": False,
        "admin_only": False,  # override cannot enable a flag absent from the plan
    }


async def test_all_enabled_when_billing_disabled_plan_layer_is_unrestricted(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession, redis: Redis, org: Organization
) -> None:
    monkeypatch.setattr(product_config, "MODULES", _BILLING_OFF)
    module_loader.get_modules.cache_clear()
    await OrgFeatureFlagRepository(session, org.id).set_override("beta_ui", enabled=False)
    await OrgFeatureFlagRepository(session, org.id).set_override("admin_only", enabled=True)

    result = await FeatureFlagService(session, redis).all_enabled(org)

    # Matches is_enabled's own billing-disabled behaviour: the plan layer
    # never denies a flag (there's no plan to consult), so an explicit
    # override is the only thing that can turn a flag off here.
    assert result == {"beta_ui": False, "admin_only": True}


async def test_all_enabled_cache_hit_skips_the_database(
    session: AsyncSession, redis: Redis, org: Organization, engine: AsyncEngine
) -> None:
    await _make_active_plan_and_subscription(session, org, {"advanced_reports": 1})
    service = FeatureFlagService(session, redis)
    await service.all_enabled(org)  # populates the cache

    query_count = 0

    def _count_queries(*_args: object, **_kwargs: object) -> None:
        nonlocal query_count
        query_count += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _count_queries)
    try:
        cached = await service.all_enabled(org)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _count_queries)

    assert cached == {"advanced_reports": True}
    assert query_count == 0


# --- invalidate / set_override / delete_override -----------------------------------


async def test_invalidate_with_nothing_cached_is_noop(
    session: AsyncSession, redis: Redis, org: Organization
) -> None:
    await FeatureFlagService(session, redis).invalidate(org.id)  # must not raise


async def test_invalidate_clears_per_flag_and_all_keys(
    session: AsyncSession, redis: Redis, org: Organization
) -> None:
    await _make_active_plan_and_subscription(session, org, {"advanced_reports": 1})
    service = FeatureFlagService(session, redis)
    await service.is_enabled(org, "advanced_reports")
    await service.all_enabled(org)
    assert await redis.get(f"feature_flags:{org.id}:advanced_reports") is not None
    assert await redis.get(f"feature_flags:{org.id}:all") is not None

    await service.invalidate(org.id)

    assert await redis.get(f"feature_flags:{org.id}:advanced_reports") is None
    assert await redis.get(f"feature_flags:{org.id}:all") is None


async def test_set_override_invalidates_cache(
    session: AsyncSession, redis: Redis, org: Organization
) -> None:
    await _make_active_plan_and_subscription(session, org, {"advanced_reports": 1})
    service = FeatureFlagService(session, redis)
    assert await service.is_enabled(org, "advanced_reports") is True

    await service.set_override(org.id, "advanced_reports", enabled=False)

    assert await redis.get(f"feature_flags:{org.id}:advanced_reports") is None
    assert await service.is_enabled(org, "advanced_reports") is False


async def test_delete_override_invalidates_cache_and_reverts_to_plan_default(
    session: AsyncSession, redis: Redis, org: Organization
) -> None:
    await _make_active_plan_and_subscription(session, org, {"advanced_reports": 1})
    service = FeatureFlagService(session, redis)
    await service.set_override(org.id, "advanced_reports", enabled=False)
    assert await service.is_enabled(org, "advanced_reports") is False

    await service.delete_override(org.id, "advanced_reports")

    assert await redis.get(f"feature_flags:{org.id}:advanced_reports") is None
    assert await service.is_enabled(org, "advanced_reports") is True
