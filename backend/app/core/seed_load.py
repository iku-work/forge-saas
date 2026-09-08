"""T-223: seeds a dedicated org with realistic volumes (1k members, 10k
notifications, several hundred files) so tests/load/'s k6 scripts exercise
real cursor pagination instead of listing an empty page every iteration.

Run via `make seed-load` (`python -m app.core.seed_load`). Idempotent at the
org level only: if LOAD_ORG_SLUG already exists, the whole bulk-population
step is skipped rather than re-checked row by row -- checking ~11k rows by
natural key on every re-run isn't worth the round trips for a fixed-size
synthetic dataset (contrast app.core.seed's per-row idempotency, which
guards a handful of rows).

Seeds 50 real, login-capable accounts (`load-user-0..49@example.com`, shared
password) rather than one hammered account: k6 scripts authenticate once per
VU in setup() and each VU keeps its own account for the whole run, which (a)
matches the T-223 spec's "distinct virtual users" requirement for
login-burst.js and (b) keeps app.middleware.rate_limit's per-user api_limit
(100/minute, keyed by user id once authenticated) from being shared across
concurrently-running VUs the way it would if they all authenticated as one
user. The other 950 members are unusable filler (random shared password
hash, never logged in) -- they only need to exist so GET .../members has
1k rows to paginate through.
"""

import asyncio
import secrets
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import SQLModel

from app.core.config import get_settings
from app.core.db import async_session_factory, dispose_engine
from app.core.module_loader import is_enabled
from app.modules.admin.service import AdminBillingOverrideService
from app.modules.auth.models import User
from app.modules.auth.permissions import MemberRole
from app.modules.auth.repository import UserRepository
from app.modules.auth.utils import hash_password
from app.modules.notifications.models import Notification
from app.modules.storage.models import StoredFile
from app.modules.tenancy.models import Membership, Organization
from app.modules.tenancy.repository import OrganizationRepository
from app.modules.tenancy.service import TenancyService

LOAD_ORG_NAME = "Load Test Org"
# TenancyService.create_org derives the slug from LOAD_ORG_NAME itself
# (app.modules.tenancy.service._slugify) rather than taking one directly --
# this must match what that produces, or the org-level idempotency check
# below never finds the org it just created and re-seeds 11k+ rows on every
# run instead of skipping.
LOAD_ORG_SLUG = "load-test-org"

# tests/load/*.js log in as load-user-{0..LOAD_USER_COUNT-1}@example.com --
# keep this in sync with the LOAD_USER_COUNT constant each script reads via
# __ENV.LOAD_USER_COUNT (default matches this).
LOAD_USER_COUNT = 50
LOAD_USER_PASSWORD = "LoadTestPass123!"  # noqa: S105 — documented load-test-only login, not a real secret

_FILLER_MEMBER_COUNT = 1000 - LOAD_USER_COUNT
_NOTIFICATION_COUNT = 10_000
_FILE_COUNT = 300
_BULK_CHUNK = 1000

# sniff-exempt (app.modules.storage.service) -- no real bytes needed
_CONTENT_TYPES = ("text/plain", "text/csv")


def _load_user_email(i: int) -> str:
    return f"load-user-{i}@example.com"


async def _ensure_pool_users(users: UserRepository) -> list[User]:
    hashed = hash_password(LOAD_USER_PASSWORD)
    pool: list[User] = []
    for i in range(LOAD_USER_COUNT):
        email = _load_user_email(i)
        user = await users.get_by_email(email)
        if user is None:
            user = await users.create(
                email=email,
                hashed_password=hashed,
                email_verified_at=datetime.now(UTC),
            )
        pool.append(user)
    return pool


async def _bulk_add(session: AsyncSession, rows: Sequence[SQLModel]) -> None:
    for start in range(0, len(rows), _BULK_CHUNK):
        session.add_all(rows[start : start + _BULK_CHUNK])
        await session.flush()


async def _seed_filler_members(session: AsyncSession, org_id: UUID) -> None:
    # Shared, unusable password hash -- these accounts only exist to pad
    # GET /orgs/{id}/members, tests/load/*.js never logs in as one, so
    # hashing a real password per row (~50-100ms each, argon2) would add
    # minutes to seeding for zero benefit.
    dummy_hash = hash_password(secrets.token_urlsafe(24))
    filler_users = [
        User(
            email=f"load-filler-{i}@example.com",
            hashed_password=dummy_hash,
            email_verified_at=datetime.now(UTC),
        )
        for i in range(_FILLER_MEMBER_COUNT)
    ]
    await _bulk_add(session, filler_users)

    memberships = [
        Membership(user_id=u.id, org_id=org_id, role=MemberRole.MEMBER) for u in filler_users
    ]
    await _bulk_add(session, memberships)


async def _seed_notifications(session: AsyncSession, org_id: UUID, pool: list[User]) -> None:
    per_user = _NOTIFICATION_COUNT // len(pool)
    notifications = [
        Notification(
            org_id=org_id,
            user_id=user.id,
            type="load_test",
            title=f"Load test notification {n}",
            body="Seeded by make seed-load (T-223) for browse.js cursor pagination.",
            channels=["in_app"],
        )
        for user in pool
        for n in range(per_user)
    ]
    await _bulk_add(session, notifications)


async def _seed_files(session: AsyncSession, org_id: UUID, pool: list[User]) -> None:
    files = [
        StoredFile(
            org_id=org_id,
            uploaded_by=pool[i % len(pool)].id,
            key=f"loadtest/{org_id}/{uuid4()}.txt",
            filename=f"load-test-file-{i}.txt",
            content_type=_CONTENT_TYPES[i % len(_CONTENT_TYPES)],
            size_bytes=1024,
            status="ready",
            bucket="default",
        )
        for i in range(_FILE_COUNT)
    ]
    await _bulk_add(session, files)


async def _grant_storage_entitlement(
    session: AsyncSession, redis: Redis, org: Organization, actor_id: UUID
) -> None:
    # New orgs have no Subscription row (TenancyService.create_org doesn't
    # create one), so they resolve to the in-memory _FREE_PLAN. That tier
    # does grant storage_gb, but only 1GB with a 10MB per-file cap -- the
    # load fixtures below push well past both, so presign.js's POST
    # /files/presign would 402 on quota partway through a run without this.
    # Same admin-override mechanism app.core.seed_pentest.py already uses for
    # its own synthetic org, not a workaround specific to this script.
    if not is_enabled("billing"):
        return
    await AdminBillingOverrideService(session, redis).set_plan(
        org=org,
        plan_slug="pro",
        billing_interval="monthly",
        free_override=True,
        expires_at=None,
        actor_id=actor_id,
        ip=None,
        ua="seed_load",
    )


async def seed_load(session: AsyncSession, redis: Redis) -> None:
    orgs = OrganizationRepository(session)
    if await orgs.get_by_slug(LOAD_ORG_SLUG) is not None:
        return

    users = UserRepository(session)
    pool = await _ensure_pool_users(users)

    tenancy = TenancyService(session)
    org = await tenancy.create_org(pool[0], LOAD_ORG_NAME)
    pool_memberships = [
        Membership(user_id=user.id, org_id=org.id, role=MemberRole.ADMIN) for user in pool[1:]
    ]
    await _bulk_add(session, pool_memberships)

    await _grant_storage_entitlement(session, redis, org, pool[0].id)
    await _seed_filler_members(session, org.id)
    await _seed_notifications(session, org.id, pool)
    await _seed_files(session, org.id, pool)


async def _main() -> None:
    settings = get_settings()
    redis = Redis.from_url(str(settings.REDIS_URL))
    try:
        async with async_session_factory() as session:
            await seed_load(session, redis)
            await session.commit()
    finally:
        await redis.aclose()
    await dispose_engine()
    print(  # noqa: T201
        f"Load-test org ready: {LOAD_ORG_SLUG} "
        f"({LOAD_USER_COUNT} login accounts load-user-0..{LOAD_USER_COUNT - 1}@example.com, "
        f"password {LOAD_USER_PASSWORD!r})"
    )


if __name__ == "__main__":
    asyncio.run(_main())
