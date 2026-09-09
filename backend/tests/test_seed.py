from collections.abc import Generator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import module_loader
from app.core.modules import EnabledModules, ModuleConfig
from app.core.seed import DEMO_ORG_NAME, DEMO_ORG_SLUG, SUPERADMIN_EMAIL, seed
from app.modules.auth.models import User
from app.modules.billing.models import Plan
from app.modules.tenancy.models import Organization
from product import config as product_config

# T-291: this file needs billing enabled -- its routes, its tables, or
# both. CI's module-matrix job deselects it in the combination that turns
# every optional module off.
pytestmark = pytest.mark.module_dependent


@pytest.fixture(autouse=True)
def _clear_modules_cache() -> Generator[None]:
    module_loader.get_modules.cache_clear()
    yield
    module_loader.get_modules.cache_clear()


async def test_seed_creates_plans_superadmin_and_demo_org(session: AsyncSession) -> None:
    await seed(session)
    await session.commit()

    plans = (await session.execute(select(Plan))).scalars().all()
    assert {p.slug for p in plans} == {"free", "pro"}

    superadmin = (
        await session.execute(select(User).where(User.email == SUPERADMIN_EMAIL))
    ).scalar_one()
    assert superadmin.is_superadmin is True
    assert superadmin.email_verified_at is not None

    org = (
        await session.execute(select(Organization).where(Organization.slug == DEMO_ORG_SLUG))
    ).scalar_one()
    assert org.name == DEMO_ORG_NAME


async def test_seed_is_idempotent(session: AsyncSession) -> None:
    await seed(session)
    await session.commit()
    await seed(session)
    await session.commit()

    plans = (await session.execute(select(Plan))).scalars().all()
    assert len(plans) == 2

    users = (
        (await session.execute(select(User).where(User.email == SUPERADMIN_EMAIL))).scalars().all()
    )
    assert len(users) == 1

    orgs = (
        (await session.execute(select(Organization).where(Organization.slug == DEMO_ORG_SLUG)))
        .scalars()
        .all()
    )
    assert len(orgs) == 1


async def test_seed_skips_plans_when_billing_disabled(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession
) -> None:
    monkeypatch.setattr(
        product_config,
        "MODULES",
        EnabledModules(
            billing=ModuleConfig(enabled=False),
            notifications=ModuleConfig(enabled=False),
            file_storage=ModuleConfig(enabled=False),
        ),
    )
    module_loader.get_modules.cache_clear()

    await seed(session)
    await session.commit()

    plans = (await session.execute(select(Plan))).scalars().all()
    assert plans == []

    superadmin = (
        await session.execute(select(User).where(User.email == SUPERADMIN_EMAIL))
    ).scalar_one()
    assert superadmin.is_superadmin is True

    org = (
        await session.execute(select(Organization).where(Organization.slug == DEMO_ORG_SLUG))
    ).scalar_one()
    assert org.name == DEMO_ORG_NAME
