"""
Verifies T-026's tenancy repository layer: OrganizationRepository,
MembershipRepository, and InvitationRepository. Uses the same real-Postgres
testcontainers pattern as test_auth_repository.py — a function-scoped engine
against tables created directly from the SQLModel metadata, no Alembic.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

from app.core.exceptions import NotFoundError
from app.modules.auth.models import User
from app.modules.auth.permissions import MemberRole
from app.modules.billing.models import Plan
from app.modules.tenancy.models import Invitation, Membership, Organization
from app.modules.tenancy.repository import (
    InvitationRepository,
    MembershipRepository,
    OrganizationRepository,
)
from tests._database import DatabaseHandle

_TABLES = [
    User.__table__,
    Plan.__table__,
    Organization.__table__,
    Membership.__table__,
    Invitation.__table__,
]


def _future(hours: int = 1) -> datetime:
    return datetime.now(UTC) + timedelta(hours=hours)


def _past(hours: int = 1) -> datetime:
    return datetime.now(UTC) - timedelta(hours=hours)


# Function-scoped (not module-scoped): see test_auth_repository.py — an
# asyncpg-backed engine can't be reused across pytest-asyncio's per-test loop.
@pytest.fixture
async def test_engine(postgres_container: DatabaseHandle) -> AsyncGenerator[AsyncEngine, None]:
    url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all, tables=_TABLES)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all, tables=_TABLES)
    await engine.dispose()


@pytest.fixture
async def session(test_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s


@pytest.fixture
def org_repo(session: AsyncSession) -> OrganizationRepository:
    return OrganizationRepository(session)


@pytest.fixture
def membership_repo(session: AsyncSession) -> MembershipRepository:
    return MembershipRepository(session)


@pytest.fixture
def invitation_repo(session: AsyncSession) -> InvitationRepository:
    return InvitationRepository(session)


@pytest.fixture
async def org(org_repo: OrganizationRepository) -> Organization:
    return await org_repo.create(name="Acme", slug=f"acme-{uuid4().hex[:8]}")


@pytest.fixture
async def user(session: AsyncSession) -> User:
    u = User(email=f"user-{uuid4()}@example.com")
    session.add(u)
    await session.flush()
    return u


# --- OrganizationRepository ----------------------------------------------


async def test_get_by_id_returns_org(org_repo: OrganizationRepository, org: Organization) -> None:
    fetched = await org_repo.get_by_id(org.id)

    assert fetched is not None
    assert fetched.slug == org.slug


async def test_get_by_id_returns_none_for_missing(org_repo: OrganizationRepository) -> None:
    assert await org_repo.get_by_id(uuid4()) is None


async def test_get_by_id_excludes_soft_deleted(
    org_repo: OrganizationRepository, org: Organization, session: AsyncSession
) -> None:
    org.deleted_at = datetime.now(UTC)
    await session.flush()

    assert await org_repo.get_by_id(org.id) is None


async def test_get_by_slug_returns_org(org_repo: OrganizationRepository, org: Organization) -> None:
    fetched = await org_repo.get_by_slug(org.slug)

    assert fetched is not None
    assert fetched.id == org.id


async def test_get_by_slug_returns_none_for_unknown_slug(
    org_repo: OrganizationRepository,
) -> None:
    assert await org_repo.get_by_slug("does-not-exist") is None


async def test_get_by_slug_excludes_soft_deleted(
    org_repo: OrganizationRepository, org: Organization, session: AsyncSession
) -> None:
    org.deleted_at = datetime.now(UTC)
    await session.flush()

    assert await org_repo.get_by_slug(org.slug) is None


async def test_create_persists_org(org_repo: OrganizationRepository) -> None:
    created = await org_repo.create(name="Globex", slug="globex")

    assert created.id is not None
    assert created.name == "Globex"
    assert created.slug == "globex"


async def test_list_for_user_returns_orgs_user_belongs_to(
    org_repo: OrganizationRepository,
    membership_repo: MembershipRepository,
    user: User,
    org: Organization,
) -> None:
    await membership_repo.create(user_id=user.id, org_id=org.id, role=MemberRole.OWNER)

    orgs = await org_repo.list_for_user(user.id)

    assert [o.id for o in orgs] == [org.id]


async def test_list_for_user_excludes_other_users_orgs(
    org_repo: OrganizationRepository,
    membership_repo: MembershipRepository,
    user: User,
    org: Organization,
) -> None:
    other_org = await org_repo.create(name="Other", slug=f"other-{uuid4().hex[:8]}")
    other_user = User(email=f"other-{uuid4()}@example.com")
    org_repo.session.add(other_user)
    await org_repo.session.flush()
    await membership_repo.create(user_id=other_user.id, org_id=other_org.id, role=MemberRole.OWNER)
    await membership_repo.create(user_id=user.id, org_id=org.id, role=MemberRole.OWNER)

    orgs = await org_repo.list_for_user(user.id)

    assert [o.id for o in orgs] == [org.id]


async def test_list_for_user_excludes_soft_deleted_orgs(
    org_repo: OrganizationRepository,
    membership_repo: MembershipRepository,
    user: User,
    org: Organization,
    session: AsyncSession,
) -> None:
    await membership_repo.create(user_id=user.id, org_id=org.id, role=MemberRole.OWNER)
    org.deleted_at = datetime.now(UTC)
    await session.flush()

    assert await org_repo.list_for_user(user.id) == []


async def test_get_or_raise_returns_org(
    org_repo: OrganizationRepository, org: Organization
) -> None:
    fetched = await org_repo.get_or_raise(org.id)

    assert fetched.id == org.id


async def test_get_or_raise_raises_not_found_for_missing(
    org_repo: OrganizationRepository,
) -> None:
    with pytest.raises(NotFoundError):
        await org_repo.get_or_raise(uuid4())


async def test_update_modifies_fields(org_repo: OrganizationRepository, org: Organization) -> None:
    updated = await org_repo.update(org.id, name="Renamed", logo_url="https://example.com/logo.png")

    assert updated.name == "Renamed"
    assert updated.logo_url == "https://example.com/logo.png"


async def test_update_raises_not_found_for_missing(org_repo: OrganizationRepository) -> None:
    with pytest.raises(NotFoundError):
        await org_repo.update(uuid4(), name="Renamed")


# --- MembershipRepository --------------------------------------------------


async def test_membership_create_persists(
    membership_repo: MembershipRepository, user: User, org: Organization
) -> None:
    created = await membership_repo.create(user_id=user.id, org_id=org.id, role=MemberRole.OWNER)

    assert created.id is not None
    assert created.role == MemberRole.OWNER
    assert created.invited_by is None


async def test_membership_get_by_id_returns_membership(
    membership_repo: MembershipRepository, user: User, org: Organization
) -> None:
    created = await membership_repo.create(user_id=user.id, org_id=org.id, role=MemberRole.MEMBER)

    fetched = await membership_repo.get_by_id(created.id)

    assert fetched is not None
    assert fetched.id == created.id


async def test_membership_get_by_id_returns_none_for_missing(
    membership_repo: MembershipRepository,
) -> None:
    assert await membership_repo.get_by_id(uuid4()) is None


async def test_get_by_user_and_org_returns_membership(
    membership_repo: MembershipRepository, user: User, org: Organization
) -> None:
    created = await membership_repo.create(user_id=user.id, org_id=org.id, role=MemberRole.ADMIN)

    fetched = await membership_repo.get_by_user_and_org(user.id, org.id)

    assert fetched is not None
    assert fetched.id == created.id


async def test_get_by_user_and_org_returns_none_for_non_member(
    membership_repo: MembershipRepository, user: User, org: Organization
) -> None:
    assert await membership_repo.get_by_user_and_org(user.id, org.id) is None


async def test_list_for_org_orders_newest_first_and_paginates_without_duplicates(
    membership_repo: MembershipRepository, org_repo: OrganizationRepository, session: AsyncSession
) -> None:
    org = await org_repo.create(name="Paginate Co", slug=f"paginate-{uuid4().hex[:8]}")
    # Explicit, strictly increasing created_at per row -- five inserts in a
    # tight loop can otherwise land on the same wall-clock tick under a
    # contended/virtualized host, at which point list_for_org's `ORDER BY
    # created_at DESC, id DESC` falls back to its `id` tiebreaker (a random
    # UUID4), which no longer agrees with insertion order. That's what made
    # this test flaky -- it's exercising the same ORDER BY clause's behavior
    # either way, just deterministically instead of depending on clock
    # resolution. MembershipRepository.create() has no created_at param (no
    # real caller ever needs one), so these rows are constructed directly.
    base_time = datetime.now(UTC)
    created_ids = []
    for i in range(5):
        u = User(email=f"page-{i}-{uuid4()}@example.com")
        session.add(u)
        await session.flush()
        m = Membership(
            user_id=u.id,
            org_id=org.id,
            role=MemberRole.MEMBER,
            created_at=base_time + timedelta(seconds=i),
        )
        session.add(m)
        await session.flush()
        created_ids.append(m.id)

    page1, cursor1 = await membership_repo.list_for_org(org.id, limit=2)
    page2, cursor2 = await membership_repo.list_for_org(org.id, after=cursor1, limit=2)
    page3, cursor3 = await membership_repo.list_for_org(org.id, after=cursor2, limit=2)

    all_ids = [m.id for m in page1 + page2 + page3]
    assert all_ids == list(reversed(created_ids))  # newest first
    assert len(set(all_ids)) == len(all_ids)  # no duplicates across pages
    assert cursor3 is None  # no more pages


async def test_list_for_org_excludes_other_orgs(
    membership_repo: MembershipRepository,
    org_repo: OrganizationRepository,
    user: User,
    org: Organization,
) -> None:
    other_org = await org_repo.create(name="Other", slug=f"other-{uuid4().hex[:8]}")
    await membership_repo.create(user_id=user.id, org_id=other_org.id, role=MemberRole.OWNER)

    items, cursor = await membership_repo.list_for_org(org.id)

    assert items == []
    assert cursor is None


async def test_set_role_updates_role(
    membership_repo: MembershipRepository, user: User, org: Organization
) -> None:
    created = await membership_repo.create(user_id=user.id, org_id=org.id, role=MemberRole.MEMBER)

    updated = await membership_repo.set_role(created.id, MemberRole.ADMIN)

    assert updated.role == MemberRole.ADMIN
    refetched = await membership_repo.get_by_id(created.id)
    assert refetched is not None
    assert refetched.role == MemberRole.ADMIN


async def test_set_role_raises_not_found_for_missing(
    membership_repo: MembershipRepository,
) -> None:
    with pytest.raises(NotFoundError):
        await membership_repo.set_role(uuid4(), MemberRole.ADMIN)


async def test_remove_hard_deletes_membership(
    membership_repo: MembershipRepository, user: User, org: Organization
) -> None:
    created = await membership_repo.create(user_id=user.id, org_id=org.id, role=MemberRole.MEMBER)

    await membership_repo.remove(created.id)

    assert await membership_repo.get_by_id(created.id) is None


async def test_remove_raises_not_found_for_missing(membership_repo: MembershipRepository) -> None:
    with pytest.raises(NotFoundError):
        await membership_repo.remove(uuid4())


async def test_count_by_org_and_role_counts_only_matching_role(
    membership_repo: MembershipRepository,
    org_repo: OrganizationRepository,
    session: AsyncSession,
) -> None:
    org = await org_repo.create(name="Counters", slug=f"counters-{uuid4().hex[:8]}")
    for role in (MemberRole.OWNER, MemberRole.OWNER, MemberRole.MEMBER):
        u = User(email=f"count-{uuid4()}@example.com")
        session.add(u)
        await session.flush()
        await membership_repo.create(user_id=u.id, org_id=org.id, role=role)

    assert await membership_repo.count_by_org_and_role(org.id, MemberRole.OWNER) == 2
    assert await membership_repo.count_by_org_and_role(org.id, MemberRole.MEMBER) == 1
    assert await membership_repo.count_by_org_and_role(org.id, MemberRole.ADMIN) == 0


async def test_count_by_org_and_role_excludes_other_orgs(
    membership_repo: MembershipRepository,
    org_repo: OrganizationRepository,
    user: User,
    org: Organization,
) -> None:
    other_org = await org_repo.create(name="Isolated", slug=f"isolated-{uuid4().hex[:8]}")
    await membership_repo.create(user_id=user.id, org_id=other_org.id, role=MemberRole.OWNER)

    assert await membership_repo.count_by_org_and_role(org.id, MemberRole.OWNER) == 0


# --- InvitationRepository --------------------------------------------------


async def test_invitation_create_persists(
    invitation_repo: InvitationRepository, org: Organization, user: User
) -> None:
    invitation_id = uuid4()
    created = await invitation_repo.create(
        invitation_id=invitation_id,
        org_id=org.id,
        email="invitee@example.com",
        role=MemberRole.MEMBER,
        token_hash="hash-a",
        invited_by=user.id,
        expires_at=_future(),
    )

    assert created.id == invitation_id
    assert created.accepted_at is None


async def test_get_by_token_hash_returns_invitation(
    invitation_repo: InvitationRepository, org: Organization, user: User
) -> None:
    created = await invitation_repo.create(
        invitation_id=uuid4(),
        org_id=org.id,
        email="invitee@example.com",
        role=MemberRole.MEMBER,
        token_hash="hash-b",
        invited_by=user.id,
        expires_at=_future(),
    )

    fetched = await invitation_repo.get_by_token_hash("hash-b")

    assert fetched is not None
    assert fetched.id == created.id


async def test_get_by_token_hash_returns_none_for_unknown_hash(
    invitation_repo: InvitationRepository,
) -> None:
    assert await invitation_repo.get_by_token_hash("missing") is None


async def test_get_by_token_hash_excludes_expired(
    invitation_repo: InvitationRepository, org: Organization, user: User
) -> None:
    await invitation_repo.create(
        invitation_id=uuid4(),
        org_id=org.id,
        email="invitee@example.com",
        role=MemberRole.MEMBER,
        token_hash="hash-expired",
        invited_by=user.id,
        expires_at=_past(),
    )

    assert await invitation_repo.get_by_token_hash("hash-expired") is None


async def test_get_by_token_hash_excludes_accepted(
    invitation_repo: InvitationRepository, org: Organization, user: User
) -> None:
    created = await invitation_repo.create(
        invitation_id=uuid4(),
        org_id=org.id,
        email="invitee@example.com",
        role=MemberRole.MEMBER,
        token_hash="hash-accepted",
        invited_by=user.id,
        expires_at=_future(),
    )
    await invitation_repo.mark_accepted(created.id)

    assert await invitation_repo.get_by_token_hash("hash-accepted") is None


async def test_mark_accepted_sets_accepted_at(
    invitation_repo: InvitationRepository, org: Organization, user: User, session: AsyncSession
) -> None:
    created = await invitation_repo.create(
        invitation_id=uuid4(),
        org_id=org.id,
        email="invitee@example.com",
        role=MemberRole.MEMBER,
        token_hash="hash-c",
        invited_by=user.id,
        expires_at=_future(),
    )

    await invitation_repo.mark_accepted(created.id)

    await session.refresh(created)
    assert created.accepted_at is not None


async def test_mark_accepted_raises_not_found_for_missing(
    invitation_repo: InvitationRepository,
) -> None:
    with pytest.raises(NotFoundError):
        await invitation_repo.mark_accepted(uuid4())
