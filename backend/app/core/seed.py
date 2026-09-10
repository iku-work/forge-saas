"""Idempotent local-dev seed: demo plans, a superadmin user, a demo org.

Run via `make seed` (`python -m app.core.seed`). Every step checks for an
existing row by its natural key before creating anything, so re-running
never produces duplicates.
"""

import asyncio
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import async_session_factory, dispose_engine
from app.core.module_loader import is_enabled
from app.modules.auth.models import User
from app.modules.auth.repository import UserRepository
from app.modules.auth.utils import hash_password
from app.modules.billing.models import Plan
from app.modules.billing.repository import PlanRepository
from app.modules.tenancy.repository import OrganizationRepository
from app.modules.tenancy.service import TenancyService
from app.workers.jobs.notifications import backfill_notification_preferences_for_session

SUPERADMIN_EMAIL = "admin@example.com"
SUPERADMIN_PASSWORD = "DevPassword123!"  # noqa: S105 — documented local-dev-only seed login, not a real secret
DEMO_ORG_NAME = "Forge Demo"
DEMO_ORG_SLUG = "forge-demo"

# T-222: a dedicated account for the Maestro mobile E2E suite (.maestro/), kept
# separate from SUPERADMIN_EMAIL so the invitation the deep-link flow sends
# doesn't pile up under the shared demo org over repeated runs.
MOBILE_E2E_EMAIL = "mobile-e2e@example.com"
MOBILE_E2E_PASSWORD = "MobileE2ePass123!"  # noqa: S105 — documented local-dev-only seed login, not a real secret
MOBILE_E2E_ORG_NAME = "Mobile E2E"
MOBILE_E2E_ORG_SLUG = "mobile-e2e"


async def _ensure_plan(plans: PlanRepository, session: AsyncSession, plan: Plan) -> None:
    if await plans.get_by_slug(plan.slug) is not None:
        return
    session.add(plan)


async def _ensure_superadmin(users: UserRepository) -> User:
    existing = await users.get_by_email(SUPERADMIN_EMAIL)
    if existing is not None:
        return existing
    return await users.create(
        email=SUPERADMIN_EMAIL,
        hashed_password=hash_password(SUPERADMIN_PASSWORD),
        email_verified_at=datetime.now(UTC),
        is_superadmin=True,
    )


async def _ensure_mobile_e2e_user(users: UserRepository) -> User:
    existing = await users.get_by_email(MOBILE_E2E_EMAIL)
    if existing is not None:
        return existing
    return await users.create(
        email=MOBILE_E2E_EMAIL,
        hashed_password=hash_password(MOBILE_E2E_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )


async def seed(session: AsyncSession) -> None:
    if is_enabled("billing"):
        # DEVELOPMENT PLANS ONLY. `price_free_monthly` and friends are not
        # Stripe ids and never will be -- a dev machine has no Stripe account
        # to hold real ones, and these exist so the billing UI has rows to
        # render. Seeding a real deployment with them is how a production
        # database ends up unable to sell anything: checkout answers 502, and
        # the webhook logs `billing.webhook_unknown_price` and returns,
        # stranding a paying customer on the free tier.
        #
        # Real environments use `make sync-plans` (app.core.sync_plans), which
        # resolves the lookup keys in product/config.py's PLANS against Stripe
        # and overwrites these rows with the ids it finds. It is safe to run
        # over a seeded database and is the documented deploy step.
        plans = PlanRepository(session)
        await _ensure_plan(
            plans,
            session,
            Plan(
                name="Free",
                slug="free",
                stripe_price_id_monthly="price_free_monthly",
                stripe_price_id_yearly="price_free_yearly",
                price_monthly=0,
                price_yearly=0,
                currency="usd",
                features={"seats": 3, "storage_gb": 1, "api_calls": 1000},
            ),
        )
        await _ensure_plan(
            plans,
            session,
            Plan(
                name="Pro",
                slug="pro",
                stripe_price_id_monthly="price_pro_monthly",
                stripe_price_id_yearly="price_pro_yearly",
                price_monthly=4900,
                price_yearly=49000,
                currency="usd",
                features={
                    "seats": 25,
                    "storage_gb": 50,
                    "api_calls": 100000,
                    "advanced_reports": True,
                    "file_storage": True,
                },
            ),
        )
        await session.flush()

    superadmin = await _ensure_superadmin(UserRepository(session))

    orgs = OrganizationRepository(session)
    if await orgs.get_by_slug(DEMO_ORG_SLUG) is None:
        await TenancyService(session).create_org(superadmin, DEMO_ORG_NAME)

    mobile_e2e_user = await _ensure_mobile_e2e_user(UserRepository(session))
    if await orgs.get_by_slug(MOBILE_E2E_ORG_SLUG) is None:
        await TenancyService(session).create_org(mobile_e2e_user, MOBILE_E2E_ORG_NAME)

    await backfill_notification_preferences_for_session(session)


async def _main() -> None:
    async with async_session_factory() as session:
        await seed(session)
        await session.commit()
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(_main())
