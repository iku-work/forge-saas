"""
FeatureFlagService (T-120): the single source of truth for "is feature X
enabled for org Y", combining Plan.features (billing-gated catalog
entitlements) with OrgFeatureFlag per-org overrides (admin/support
overrides, T-122) behind a 60s Redis cache.

Two-layer resolution, per the P2-C architecture rule:
  1. Plan layer: bool(plan.features.get(flag)) via
     app.modules.billing.dependencies.resolve_org_subscription. When billing
     is disabled (T-065: no Plan/Subscription tables at all), this layer is
     always True -- there's no plan to consult, so it can never be the
     reason a flag is off; only the override layer can gate a flag for a
     non-billing product.
  2. Override layer: OrgFeatureFlagRepository's per-org row for that flag.
     Absent -> True (doesn't touch the plan layer's result). Explicit False
     -> always wins. An override can only ever narrow access, never grant a
     flag the plan layer denied ("override cannot enable it").

app.modules.billing.dependencies imports this module at the top level (its
require_feature delegates its boolean gate here); this module imports
billing.dependencies back, but only lazily inside method bodies, to avoid a
circular import at module-load time -- by the time these methods run,
billing.dependencies has always already finished importing.
"""

import json
from typing import cast
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache, cache_keys
from app.core.module_loader import is_enabled as is_module_enabled
from app.modules.feature_flags.repository import OrgFeatureFlagRepository
from app.modules.tenancy.models import Organization

_CACHE_TTL_SECONDS = cache.DEFAULT_TTL_SECONDS


class FeatureFlagService:
    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self._session = session
        self._redis = redis

    async def is_enabled(self, org: Organization, flag: str) -> bool:
        cache_key = cache_keys.feature_flag_key(org.id, flag)
        cached = await self._redis.get(cache_key)
        if cached is not None:
            return bool(int(cached))

        plan_allows = await self._plan_allows(org, flag)
        overrides = await OrgFeatureFlagRepository(self._session, org.id).get_overrides()
        override = overrides.get(flag)
        result = plan_allows and (override if override is not None else True)

        await self._redis.set(cache_key, int(result), ex=_CACHE_TTL_SECONDS)
        return result

    async def all_enabled(self, org: Organization) -> dict[str, bool]:
        cache_key = cache_keys.feature_flags_all_key(org.id)
        cached = await self._redis.get(cache_key)
        if cached is not None:
            return cast("dict[str, bool]", json.loads(cached))

        # billing_gated computed once and applied per-flag below, matching
        # is_enabled/_plan_allows's "billing disabled -> plan layer is
        # unrestricted" rule exactly -- bool(plan_features.get(name)) alone
        # would wrongly read as False for every flag once billing is
        # disabled (plan_features is always {} then), even one an override
        # explicitly grants.
        billing_gated = is_module_enabled("billing")
        plan_features = await self._plan_features(org)
        overrides = await OrgFeatureFlagRepository(self._session, org.id).get_overrides()
        # A flag absent from the plan is False regardless of an override
        # (see module docstring) -- still included in the map (rather than
        # omitted) so an admin override on a flag the plan doesn't grant is
        # visible as "off", not silently missing.
        flag_names = set(plan_features) | set(overrides)
        result = {
            name: (bool(plan_features.get(name)) if billing_gated else True)
            and overrides.get(name, True)
            for name in flag_names
        }

        await self._redis.set(cache_key, json.dumps(result), ex=_CACHE_TTL_SECONDS)
        return result

    async def invalidate(self, org_id: UUID) -> None:
        # T-240: also busts the org's cached Subscription+Plan, since every
        # flag answer here is derived from it -- see
        # app.core.cache.invalidate_org_entitlements. Every caller that
        # changes an org's plan already calls this, so coupling the two here
        # (rather than asking each of those call sites to remember a second
        # call) is what keeps the plan cache from ever outliving its flags.
        await cache.invalidate_org_entitlements(self._redis, org_id)

    async def set_override(self, org_id: UUID, flag_name: str, enabled: bool) -> None:
        await OrgFeatureFlagRepository(self._session, org_id).set_override(
            flag_name, enabled=enabled
        )
        await self.invalidate(org_id)

    async def delete_override(self, org_id: UUID, flag_name: str) -> None:
        await OrgFeatureFlagRepository(self._session, org_id).delete_override(flag_name)
        await self.invalidate(org_id)

    async def _plan_allows(self, org: Organization, flag: str) -> bool:
        if not is_module_enabled("billing"):
            return True
        plan_features = await self._plan_features(org)
        return bool(plan_features.get(flag))

    async def _plan_features(self, org: Organization) -> dict[str, int]:
        if not is_module_enabled("billing"):
            return {}
        # Lazy import: see module docstring on avoiding a circular import
        # with billing.dependencies.
        from app.modules.billing.dependencies import resolve_org_subscription  # noqa: PLC0415

        org_sub = await resolve_org_subscription(org, self._session, self._redis)
        return org_sub.plan.features
