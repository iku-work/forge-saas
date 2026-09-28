"""
Verifies T-036's purge_user_data ARQ job: hard-deletes every row referencing
a soft-deleted user across the tables agents.md's T-036 enumerates (minus
Notification/StoredFile, which don't exist until T-101/T-110 -- see
app.workers.jobs.gdpr's module docstring), nulls out other members'
Membership.invited_by pointers to the purged user instead of leaving a
dangling FK, detaches an org's Stripe customer once its last member is
purged, and is a no-op when the user isn't (or is no longer) soft-deleted.

Uses the same real-Postgres testcontainers pattern as test_worker_scheduler.py
and test_encryption_job.py: the job module's `async_session_factory` is
monkeypatched to a session factory bound to the test engine. Per
test_worker_scheduler.py's docstring, the job runs in its own session, so
every assertion after calling it goes through a brand-new `verify_session`
(never the seeding session) -- with expire_on_commit=False, the seeding
session's identity map would otherwise keep serving stale, pre-purge copies
of rows it created itself.
"""

from collections.abc import AsyncGenerator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

from app.core import module_loader
from app.core.modules import ModuleConfig
from app.modules.api_keys.models import ApiKey
from app.modules.audit.models import AuditEvent
from app.modules.auth.models import (
    EmailVerificationToken,
    MagicLinkToken,
    OAuthAccount,
    PasswordResetToken,
    TOTPSecret,
    User,
)
from app.modules.auth.models import Session as SessionModel
from app.modules.auth.permissions import MemberRole
from app.modules.billing.models import Plan
from app.modules.gdpr.models import ConsentRecord, DataExportJob
from app.modules.tenancy.models import Membership, Organization
from app.workers.jobs import gdpr as gdpr_job_module
from app.workers.jobs.gdpr import purge_user_data
from product import config as product_config
from tests._database import DatabaseHandle

_TABLES = [
    User.__table__,
    SessionModel.__table__,
    OAuthAccount.__table__,
    TOTPSecret.__table__,
    EmailVerificationToken.__table__,
    PasswordResetToken.__table__,
    MagicLinkToken.__table__,
    Plan.__table__,
    Organization.__table__,
    Membership.__table__,
    ConsentRecord.__table__,
    DataExportJob.__table__,
    AuditEvent.__table__,
    # T-310. Created here even though `public_api` ships off -- the one test
    # that needs it turns the module on, and the purge's own module check is
    # what keeps the default configuration from touching it.
    ApiKey.__table__,
]

_DELETED_BY_USER_ID = (
    DataExportJob,
    ConsentRecord,
    MagicLinkToken,
    PasswordResetToken,
    EmailVerificationToken,
    TOTPSecret,
    OAuthAccount,
    SessionModel,
    Membership,
)


def _past(days: int = 1) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


def _future(days: int = 1) -> datetime:
    return datetime.now(UTC) + timedelta(days=days)


# Function-scoped (not module-scoped): see test_auth_repository.py -- an
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


@pytest.fixture(autouse=True)
def _use_test_engine(monkeypatch: pytest.MonkeyPatch, test_engine: AsyncEngine) -> None:
    # purge_user_data opens its own session via the module-level
    # async_session_factory (no session param -- same shape as the other ARQ
    # jobs) -- point that factory at the test engine, same pattern as
    # test_encryption_job.py / test_worker_scheduler.py.
    factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(gdpr_job_module, "async_session_factory", factory)


@pytest.fixture
def session_factory(test_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as s:
        yield s


async def _make_user(session: AsyncSession, email: str, *, deleted: bool = True) -> User:
    user = User(email=email, deleted_at=_past() if deleted else None)
    session.add(user)
    await session.flush()
    return user


async def _make_org(
    session: AsyncSession, *, stripe_customer_id: str | None = "cus_123"
) -> Organization:
    org = Organization(name="Acme", slug=f"acme-{uuid4()}", stripe_customer_id=stripe_customer_id)
    session.add(org)
    await session.flush()
    return org


async def _seed_full_footprint(session: AsyncSession, user: User, org: Organization) -> None:
    """One row in every table T-036 deletes from, all referencing `user`."""
    session.add(DataExportJob(user_id=user.id))
    session.add(
        ConsentRecord(
            user_id=user.id,
            consent_type="marketing",
            granted=True,
            version="v1",
            ip_address="127.0.0.1",
            user_agent="pytest",
        )
    )
    session.add(
        AuditEvent(
            org_id=org.id, actor_id=user.id, action="user.login_success", resource_type="user"
        )
    )
    session.add(MagicLinkToken(user_id=user.id, token_hash=f"ml-{user.id}", expires_at=_future()))
    session.add(
        PasswordResetToken(user_id=user.id, token_hash=f"pr-{user.id}", expires_at=_future())
    )
    session.add(
        EmailVerificationToken(user_id=user.id, token_hash=f"ev-{user.id}", expires_at=_future())
    )
    session.add(TOTPSecret(user_id=user.id, secret_encrypted="JBSWY3DPEHPK3PXP"))
    session.add(OAuthAccount(user_id=user.id, provider="google", provider_user_id=str(user.id)))
    session.add(
        SessionModel(
            user_id=user.id,
            refresh_token_hash=f"sess-{user.id}",
            ip_address="127.0.0.1",
            user_agent="pytest",
            last_seen_at=datetime.now(UTC),
            expires_at=_future(),
            auth_time=datetime.now(UTC),
        )
    )
    session.add(Membership(user_id=user.id, org_id=org.id, role=MemberRole.MEMBER))
    await session.flush()


async def _count(
    session: AsyncSession, model: type[SQLModel], column_name: str, value: object
) -> int:
    column = getattr(model, column_name)
    result = await session.execute(select(model).where(column == value))
    return len(result.scalars().all())


# --- full sweep -------------------------------------------------------------


async def test_purge_user_data_deletes_every_referenced_row(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    org = await _make_org(session)
    target = await _make_user(session, "purge-me@example.com")
    await _seed_full_footprint(session, target, org)

    other = await _make_user(session, "keep-me@example.com", deleted=False)
    other_org = await _make_org(session, stripe_customer_id="cus_other")
    await _seed_full_footprint(session, other, other_org)
    await session.commit()

    await purge_user_data({}, user_id=target.id)

    async with session_factory() as verify_session:
        assert (
            await verify_session.execute(select(User).where(User.id == target.id))
        ).scalar_one_or_none() is None
        for model in _DELETED_BY_USER_ID:
            assert await _count(verify_session, model, "user_id", target.id) == 0
        assert await _count(verify_session, AuditEvent, "actor_id", target.id) == 0

        # The untouched user's own rows survive purge of someone else.
        assert (
            await verify_session.execute(select(User).where(User.id == other.id))
        ).scalar_one_or_none() is not None
        for model in _DELETED_BY_USER_ID:
            assert await _count(verify_session, model, "user_id", other.id) == 1
        assert await _count(verify_session, AuditEvent, "actor_id", other.id) == 1


# --- guard clauses -----------------------------------------------------------


async def test_purge_user_data_skips_user_that_is_not_soft_deleted(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user = await _make_user(session, "active@example.com", deleted=False)
    await session.commit()

    await purge_user_data({}, user_id=user.id)

    async with session_factory() as verify_session:
        assert await verify_session.get(User, user.id) is not None


async def test_purge_user_data_is_a_noop_for_unknown_user() -> None:
    # No row exists for this id -- must not raise.
    await purge_user_data({}, user_id=uuid4())


# --- invited_by nulling -------------------------------------------------------


async def test_purge_user_data_nulls_invited_by_on_other_memberships(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    org = await _make_org(session)
    inviter = await _make_user(session, "owner@example.com")
    invitee = await _make_user(session, "member@example.com", deleted=False)
    session.add(Membership(user_id=inviter.id, org_id=org.id, role=MemberRole.OWNER))
    session.add(
        Membership(user_id=invitee.id, org_id=org.id, role=MemberRole.MEMBER, invited_by=inviter.id)
    )
    await session.commit()

    await purge_user_data({}, user_id=inviter.id)

    async with session_factory() as verify_session:
        assert (
            await verify_session.execute(select(User).where(User.id == inviter.id))
        ).scalar_one_or_none() is None
        invitee_membership = (
            await verify_session.execute(select(Membership).where(Membership.user_id == invitee.id))
        ).scalar_one()
        assert invitee_membership.invited_by is None


# --- Stripe customer detachment ----------------------------------------------


async def test_purge_user_data_nulls_created_by_and_keeps_the_organizations_keys(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-310. An API key belongs to the *organization*, not to the person who
    minted it, so erasure nulls `created_by` and leaves the credential alive.

    Deleting it would take an integration down because a colleague exercised
    a right that has nothing to do with it -- which is also why
    docs/runbooks/api-keys.md makes reviewing a leaver's keys an explicit
    step rather than something the purge is assumed to have done.
    """
    monkeypatch.setattr(
        product_config,
        "MODULES",
        replace(product_config.MODULES, public_api=ModuleConfig(enabled=True)),
    )
    module_loader.get_modules.cache_clear()

    org = await _make_org(session)
    user = await _make_user(session, "leaver@example.com")
    key = ApiKey(
        org_id=org.id,
        name="billing-sync",
        prefix="abcdef123456",
        key_hash="f" * 64,
        scopes=["orgs:read"],
        created_by=user.id,
    )
    session.add(key)
    await session.commit()
    # Read the ids out before the purge: `expire_all()` below detaches every
    # attribute, and touching one afterwards would emit a lazy load from sync
    # context on an async session.
    key_id, user_id = key.id, user.id

    await purge_user_data({}, user_id=user_id)

    session.expire_all()
    surviving = await session.get(ApiKey, key_id)
    assert surviving is not None, "erasing a member must not delete their org's credentials"
    assert surviving.created_by is None
    # ...and the user really is gone, so this is not a purge that silently
    # did nothing.
    assert await session.get(User, user_id) is None
    module_loader.get_modules.cache_clear()


async def test_purge_user_data_clears_stripe_customer_when_org_has_no_members_left(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    org = await _make_org(session, stripe_customer_id="cus_lonely")
    sole_member = await _make_user(session, "lonely@example.com")
    session.add(Membership(user_id=sole_member.id, org_id=org.id, role=MemberRole.OWNER))
    await session.commit()

    await purge_user_data({}, user_id=sole_member.id)

    async with session_factory() as verify_session:
        refetched_org = await verify_session.get(Organization, org.id)
        assert refetched_org is not None
        assert refetched_org.stripe_customer_id is None


async def test_purge_user_data_keeps_stripe_customer_when_org_still_has_members(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    org = await _make_org(session, stripe_customer_id="cus_busy")
    leaving = await _make_user(session, "leaving@example.com")
    staying = await _make_user(session, "staying@example.com", deleted=False)
    session.add(Membership(user_id=leaving.id, org_id=org.id, role=MemberRole.OWNER))
    session.add(Membership(user_id=staying.id, org_id=org.id, role=MemberRole.MEMBER))
    await session.commit()

    await purge_user_data({}, user_id=leaving.id)

    async with session_factory() as verify_session:
        refetched_org = await verify_session.get(Organization, org.id)
        assert refetched_org is not None
        assert refetched_org.stripe_customer_id == "cus_busy"
        assert await verify_session.get(User, staying.id) is not None
