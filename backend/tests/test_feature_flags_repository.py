"""
Verifies T-120's OrgFeatureFlagRepository: overrides are scoped per-org (two
orgs can hold independent override state for the same flag_name, since the
table's real unique constraint is (org_id, flag_name), not flag_name alone),
set_override upserts rather than duplicating a row, and delete_override is
idempotent (deleting a flag with no override is a no-op, not an error).

Uses conftest's shared full-migration session fixture -- OrgFeatureFlag
already has a real migration (T-025), so no throwaway _TABLES engine is
needed here, same as test_storage_repository.py.
"""

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.feature_flags.repository import OrgFeatureFlagRepository
from app.modules.tenancy.models import Organization
from app.modules.tenancy.repository import OrganizationRepository


@pytest.fixture
async def org(session: AsyncSession) -> Organization:
    return await OrganizationRepository(session).create(name="Acme", slug=f"acme-{uuid4().hex[:8]}")


@pytest.fixture
async def other_org(session: AsyncSession) -> Organization:
    return await OrganizationRepository(session).create(name="Beta", slug=f"beta-{uuid4().hex[:8]}")


async def test_get_overrides_empty_for_new_org(session: AsyncSession, org: Organization) -> None:
    overrides = await OrgFeatureFlagRepository(session, org.id).get_overrides()

    assert overrides == {}


async def test_set_override_creates_row(session: AsyncSession, org: Organization) -> None:
    repo = OrgFeatureFlagRepository(session, org.id)

    await repo.set_override("advanced_reports", enabled=False)

    assert await repo.get_overrides() == {"advanced_reports": False}


async def test_set_override_on_existing_flag_updates_in_place(
    session: AsyncSession, org: Organization
) -> None:
    repo = OrgFeatureFlagRepository(session, org.id)
    await repo.set_override("advanced_reports", enabled=False)

    await repo.set_override("advanced_reports", enabled=True)

    assert await repo.get_overrides() == {"advanced_reports": True}


async def test_delete_override_removes_it(session: AsyncSession, org: Organization) -> None:
    repo = OrgFeatureFlagRepository(session, org.id)
    await repo.set_override("advanced_reports", enabled=False)

    await repo.delete_override("advanced_reports")

    assert await repo.get_overrides() == {}


async def test_delete_override_on_unknown_flag_is_noop(
    session: AsyncSession, org: Organization
) -> None:
    repo = OrgFeatureFlagRepository(session, org.id)

    await repo.delete_override("never_set")  # must not raise

    assert await repo.get_overrides() == {}


async def test_overrides_are_scoped_per_org(
    session: AsyncSession, org: Organization, other_org: Organization
) -> None:
    await OrgFeatureFlagRepository(session, org.id).set_override("advanced_reports", enabled=False)
    await OrgFeatureFlagRepository(session, other_org.id).set_override(
        "advanced_reports", enabled=True
    )

    assert await OrgFeatureFlagRepository(session, org.id).get_overrides() == {
        "advanced_reports": False
    }
    assert await OrgFeatureFlagRepository(session, other_org.id).get_overrides() == {
        "advanced_reports": True
    }
