"""
Direct repository-level tests for T-141's AdminOrgRepository.list_orgs --
covers branches test_admin_directory_router.py's HTTP-level tests don't
reach: cursor pagination across a page boundary, and the billing-disabled
fallback. is_enabled is monkeypatched directly rather than spinning up a
whole billing-disabled migration scenario the way
test_conditional_migrations.py does -- this method's branch logic doesn't
care whether the tables really exist, only what is_enabled("billing")
reports, so a lightweight patch is enough to exercise both branches.
"""

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.admin import repository as admin_repository
from app.modules.admin.repository import AdminOrgRepository
from app.modules.tenancy.models import Organization
from app.modules.tenancy.repository import OrganizationRepository


async def _make_org(session: AsyncSession, name: str) -> Organization:
    org = await OrganizationRepository(session).create(
        name=name, slug=f"{name.lower()}-{uuid4().hex[:8]}"
    )
    await session.commit()
    return org


async def test_list_orgs_paginates_without_duplicates(session: AsyncSession) -> None:
    orgs = [await _make_org(session, f"Org{i}") for i in range(3)]
    repo = AdminOrgRepository(session)

    page1, cursor1 = await repo.list_orgs(limit=2)
    assert len(page1) == 2
    assert cursor1 is not None

    page2, cursor2 = await repo.list_orgs(after=cursor1, limit=2)
    assert cursor2 is None

    ids_page1 = {row[0].id for row in page1}
    ids_page2 = {row[0].id for row in page2}
    assert ids_page1.isdisjoint(ids_page2)
    assert {org.id for org in orgs} <= ids_page1 | ids_page2


async def test_list_orgs_returns_empty_page_when_billing_disabled_and_filter_given(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _make_org(session, "Solo")
    monkeypatch.setattr(admin_repository, "is_enabled", lambda _module: False)
    repo = AdminOrgRepository(session)

    page, cursor = await repo.list_orgs(status="active")

    assert page == []
    assert cursor is None


async def test_list_orgs_lists_plain_orgs_when_billing_disabled_without_filter(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = await _make_org(session, "Solo2")
    monkeypatch.setattr(admin_repository, "is_enabled", lambda _module: False)
    repo = AdminOrgRepository(session)

    page, _ = await repo.list_orgs(q=org.slug)

    assert len(page) == 1
    assert page[0][0].id == org.id
    assert page[0][1] is None
    assert page[0][2] is None
