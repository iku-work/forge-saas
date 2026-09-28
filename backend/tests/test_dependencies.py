"""
Verifies T-021's three dependencies: get_current_user (JWT + 60s Redis
cache), get_current_org (X-Org-ID header + membership lookup), and
require_permission (role -> permission gate). The full role x permission
matrix lives in tests/test_permissions.py — this file exercises the
dependency functions themselves, called directly rather than through a real
router (T-022 doesn't exist yet).
"""

from collections.abc import AsyncGenerator
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from freezegun import freeze_time
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from starlette.requests import Request
from testcontainers.redis import RedisContainer

from app.core import cache, cache_keys
from app.core.config import get_settings
from app.core.dependencies import (
    get_current_org,
    get_current_user,
    get_redis,
    require_permission,
    require_recent_auth,
    require_superadmin,
)
from app.core.exceptions import (
    AuthenticationError,
    DomainError,
    PermissionDeniedError,
    StepUpAuthRequiredError,
)
from app.core.migrations import build_config
from app.core.migrations import upgrade as upgrade_migrations
from app.models._mixins import utcnow
from app.modules.audit.models import AuditEvent
from app.modules.auth.models import User
from app.modules.auth.permissions import MemberRole, Permission
from app.modules.auth.utils import create_access_token
from app.modules.tenancy.models import Membership, Organization
from tests._database import DatabaseHandle


@pytest.fixture(scope="module")
def redis_container() -> "RedisContainer":
    with RedisContainer("redis:7-alpine") as container:
        yield container


@pytest.fixture(scope="module")
def database_url(postgres_container: DatabaseHandle) -> str:
    # Migrated once per module via MonkeyPatch.context() (not the function-
    # scoped `monkeypatch` fixture, since this fixture is module-scoped) so
    # alembic/env.py's get_settings().DATABASE_URL points at the container
    # only for the duration of the upgrade.
    url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_URL", url)
        get_settings.cache_clear()
        upgrade_migrations(build_config())
    get_settings.cache_clear()
    return str(url)


# Function-scoped (not module-scoped): pytest-asyncio gives each test its own
# event loop, and an asyncpg-backed engine can't be reused across loops.
@pytest.fixture
async def session(database_url: str) -> AsyncGenerator[AsyncSession, None]:
    engine: AsyncEngine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
async def redis_client(redis_container: "RedisContainer") -> AsyncGenerator[Redis, None]:
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    client: Redis = Redis.from_url(f"redis://{host}:{port}/0")
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture
async def active_user(session: AsyncSession) -> User:
    user = User(email=f"user-{uuid4()}@example.com")  # is_active defaults to True
    session.add(user)
    await session.flush()
    return user


def _token_for(user: User) -> str:
    return create_access_token(user.id, None, ())


def _unique_org() -> Organization:
    return Organization(name="Acme", slug=f"acme-{uuid4().hex[:12]}")


@pytest.fixture
def request_() -> Request:
    # get_current_user only touches request.state.locale -- a bare scope is
    # enough for Starlette's Request.state to lazily back it with a dict.
    return Request(scope={"type": "http"})


# ---- get_redis -------------------------------------------------------------


async def test_get_redis_reads_from_app_state() -> None:
    fake_redis = SimpleNamespace()
    request = Request(
        scope={"type": "http", "app": SimpleNamespace(state=SimpleNamespace(redis=fake_redis))}
    )

    assert await get_redis(request) is fake_redis


# ---- get_current_user -----------------------------------------------------


async def test_get_current_user_raises_without_token(
    request_: Request, session: AsyncSession, redis_client: Redis
) -> None:
    with pytest.raises(AuthenticationError):
        await get_current_user(request=request_, token=None, session=session, redis=redis_client)


async def test_get_current_user_raises_for_garbage_token(
    request_: Request, session: AsyncSession, redis_client: Redis
) -> None:
    with pytest.raises(AuthenticationError):
        await get_current_user(
            request=request_, token="not-a-jwt", session=session, redis=redis_client
        )


async def test_get_current_user_raises_for_unknown_user(
    request_: Request, session: AsyncSession, redis_client: Redis
) -> None:
    token = create_access_token(uuid4(), None, ())

    with pytest.raises(AuthenticationError):
        await get_current_user(request=request_, token=token, session=session, redis=redis_client)


async def test_get_current_user_returns_user_on_cache_miss_and_warms_cache(
    request_: Request, active_user: User, session: AsyncSession, redis_client: Redis
) -> None:
    user = await get_current_user(
        request=request_, token=_token_for(active_user), session=session, redis=redis_client
    )

    assert user.id == active_user.id
    assert await redis_client.ttl(cache_keys.user_key(active_user.id)) > 0


async def test_get_current_user_sets_request_state_locale_from_user(
    request_: Request, session: AsyncSession, redis_client: Redis
) -> None:
    user = User(email=f"fi-{uuid4()}@example.com", locale="fi")
    session.add(user)
    await session.flush()

    await get_current_user(
        request=request_, token=_token_for(user), session=session, redis=redis_client
    )

    assert request_.state.locale == "fi"


async def test_get_current_user_returns_user_on_cache_hit_without_db_lookup(
    request_: Request, active_user: User, session: AsyncSession, redis_client: Redis
) -> None:
    token = _token_for(active_user)
    await get_current_user(
        request=request_, token=token, session=session, redis=redis_client
    )  # warms cache

    await session.delete(active_user)
    await session.flush()

    user = await get_current_user(
        request=request_, token=token, session=session, redis=redis_client
    )

    assert user.id == active_user.id  # only resolvable via the cache now


async def test_get_current_user_raises_for_inactive_user_on_cache_miss(
    request_: Request, session: AsyncSession, redis_client: Redis
) -> None:
    user = User(email=f"inactive-{uuid4()}@example.com", is_active=False)
    session.add(user)
    await session.flush()

    with pytest.raises(AuthenticationError):
        await get_current_user(
            request=request_, token=_token_for(user), session=session, redis=redis_client
        )


async def test_get_current_user_raises_for_inactive_user_on_cache_hit(
    request_: Request, session: AsyncSession, redis_client: Redis
) -> None:
    user = User(email=f"stale-{uuid4()}@example.com", is_active=False)
    session.add(user)
    await session.flush()
    await cache.write_json(redis_client, cache_keys.user_key(user.id), cache.dump(user))

    with pytest.raises(AuthenticationError):
        await get_current_user(
            request=request_, token=_token_for(user), session=session, redis=redis_client
        )


# ---- get_current_org -------------------------------------------------------


async def test_get_current_org_raises_400_when_header_missing(
    active_user: User, session: AsyncSession, redis_client: Redis
) -> None:
    with pytest.raises(DomainError) as exc_info:
        await get_current_org(user=active_user, session=session, redis=redis_client, x_org_id=None)

    assert exc_info.value.status_code == 400


async def test_get_current_org_raises_403_for_nonexistent_org(
    active_user: User, session: AsyncSession, redis_client: Redis
) -> None:
    with pytest.raises(PermissionDeniedError) as exc_info:
        await get_current_org(
            user=active_user, session=session, redis=redis_client, x_org_id=uuid4()
        )

    assert exc_info.value.status_code == 403


async def test_get_current_org_raises_403_when_org_exists_but_not_a_member(
    active_user: User, session: AsyncSession, redis_client: Redis
) -> None:
    org = _unique_org()
    session.add(org)
    await session.flush()

    with pytest.raises(PermissionDeniedError):
        await get_current_org(
            user=active_user, session=session, redis=redis_client, x_org_id=org.id
        )


async def test_get_current_org_returns_org_and_membership_for_member(
    active_user: User, session: AsyncSession, redis_client: Redis
) -> None:
    org = _unique_org()
    session.add(org)
    await session.flush()
    membership = Membership(user_id=active_user.id, org_id=org.id, role=MemberRole.MEMBER)
    session.add(membership)
    await session.flush()

    returned_org, returned_membership = await get_current_org(
        user=active_user, session=session, redis=redis_client, x_org_id=org.id
    )

    assert returned_org.id == org.id
    assert returned_membership.id == membership.id


# ---- require_permission ----------------------------------------------------


def _membership(role: MemberRole) -> Membership:
    return Membership(user_id=uuid4(), org_id=uuid4(), role=role)


def _plain_request() -> Request:
    return Request(
        scope={
            "type": "http",
            "path": "/api/v1/billing/subscription",
            "query_string": b"",
            "headers": [(b"user-agent", b"pytest")],
            "client": ("127.0.0.1", 12345),
        }
    )


async def test_require_permission_denies_member_without_billing_manage(
    session: AsyncSession,
) -> None:
    checker = require_permission(Permission.BILLING_MANAGE)
    org = _unique_org()
    membership = _membership(MemberRole.MEMBER)
    kwargs = {
        "request": _plain_request(),
        "org_and_membership": (org, membership),
        "session": session,
    }

    with pytest.raises(PermissionDeniedError) as exc_info:
        await checker(**kwargs)

    assert exc_info.value.status_code == 403


async def test_require_permission_allows_owner_with_billing_manage(session: AsyncSession) -> None:
    checker = require_permission(Permission.BILLING_MANAGE)
    org = _unique_org()
    membership = _membership(MemberRole.OWNER)
    kwargs = {
        "request": _plain_request(),
        "org_and_membership": (org, membership),
        "session": session,
    }

    result = await checker(**kwargs)

    assert result is membership


async def test_require_permission_denial_writes_audit_event(session: AsyncSession) -> None:
    checker = require_permission(Permission.BILLING_MANAGE)
    org = _unique_org()
    membership = _membership(MemberRole.MEMBER)
    kwargs = {
        "request": _plain_request(),
        "org_and_membership": (org, membership),
        "session": session,
    }

    with pytest.raises(PermissionDeniedError):
        await checker(**kwargs)

    # Scoped to this test's own org: the denial is now committed by the
    # dependency itself (otherwise the raise would unwind get_session's
    # rollback and lose it in production), and this module's session fixture
    # keeps whatever earlier tests committed.
    events = (
        (
            await session.execute(
                select(AuditEvent).where(
                    AuditEvent.action == "permission.denied", AuditEvent.org_id == org.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].actor_id == membership.user_id
    assert events[0].org_id == org.id
    assert events[0].severity == "elevated"
    assert events[0].ip_address == "127.0.0.1"
    assert events[0].user_agent == "pytest"


# ---- require_recent_auth (T-218) --------------------------------------------


async def test_require_recent_auth_allows_within_window(
    request_: Request, active_user: User, session: AsyncSession, redis_client: Redis
) -> None:
    token = _token_for(active_user)  # auth_time defaults to "now"
    await get_current_user(request=request_, token=token, session=session, redis=redis_client)
    checker = require_recent_auth(max_age_minutes=5)

    await checker(request=request_, user=active_user)  # must not raise


async def test_require_recent_auth_rejects_after_window(
    request_: Request, active_user: User, session: AsyncSession, redis_client: Redis
) -> None:
    with freeze_time(utcnow()):
        token = _token_for(active_user)
        await get_current_user(request=request_, token=token, session=session, redis=redis_client)

    checker = require_recent_auth(max_age_minutes=5)
    with (
        freeze_time(utcnow() + timedelta(minutes=10)),
        pytest.raises(StepUpAuthRequiredError) as exc_info,
    ):
        await checker(request=request_, user=active_user)

    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "step_up_auth_required"


async def test_require_recent_auth_ignores_access_token_refresh(
    request_: Request, active_user: User, session: AsyncSession, redis_client: Redis
) -> None:
    """A refreshed access token that carries forward the *original*
    auth_time (as AuthService.refresh() does -- see Session.auth_time's
    docstring) must not reset the step-up clock, unlike a token minted by a
    fresh login/2FA confirmation would."""
    stale_auth_time = utcnow() - timedelta(minutes=10)
    refreshed_token = create_access_token(active_user.id, None, (), auth_time=stale_auth_time)
    await get_current_user(
        request=request_, token=refreshed_token, session=session, redis=redis_client
    )
    checker = require_recent_auth(max_age_minutes=5)

    with pytest.raises(StepUpAuthRequiredError):
        await checker(request=request_, user=active_user)


# ---- require_superadmin (T-140) --------------------------------------------


def _admin_request() -> Request:
    return Request(
        scope={
            "type": "http",
            "path": "/api/v1/admin/orgs/acme/feature-flags",
            "query_string": b"",
            "path_params": {"org_id": "acme"},
            "headers": [],
        }
    )


async def test_require_superadmin_denies_non_superadmin(
    active_user: User, session: AsyncSession
) -> None:
    with pytest.raises(PermissionDeniedError) as exc_info:
        await require_superadmin(request=_admin_request(), user=active_user, session=session)

    assert exc_info.value.status_code == 403


async def test_require_superadmin_allows_superadmin(session: AsyncSession) -> None:
    admin = User(email=f"admin-{uuid4()}@example.com", is_superadmin=True, totp_enabled=True)
    session.add(admin)
    await session.flush()

    result = await require_superadmin(request=_admin_request(), user=admin, session=session)

    assert result is admin


# ---- require_superadmin: T-218 mandatory-2FA gate --------------------------


async def test_require_superadmin_denies_superadmin_without_2fa(session: AsyncSession) -> None:
    admin = User(email=f"admin-{uuid4()}@example.com", is_superadmin=True)
    session.add(admin)
    await session.flush()

    with pytest.raises(PermissionDeniedError) as exc_info:
        await require_superadmin(request=_admin_request(), user=admin, session=session)

    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "permission_denied"


async def test_require_superadmin_allows_superadmin_with_webauthn_only(
    session: AsyncSession,
) -> None:
    from app.modules.auth.repository import WebAuthnCredentialRepository  # noqa: PLC0415

    admin = User(email=f"admin-{uuid4()}@example.com", is_superadmin=True)
    session.add(admin)
    await session.flush()
    await WebAuthnCredentialRepository(session).create(
        user_id=admin.id,
        credential_id=f"cred-{uuid4()}",
        public_key="pub",
        sign_count=0,
        aaguid="00000000-0000-0000-0000-000000000000",
        nickname=None,
    )

    result = await require_superadmin(request=_admin_request(), user=admin, session=session)

    assert result is admin


async def test_require_superadmin_logs_admin_action_with_actor_and_path(
    session: AsyncSession,
) -> None:
    from loguru import logger as loguru_logger  # noqa: PLC0415

    admin = User(email=f"admin-{uuid4()}@example.com", is_superadmin=True, totp_enabled=True)
    session.add(admin)
    await session.flush()

    records: list[dict[str, object]] = []
    sink_id = loguru_logger.add(lambda message: records.append(message.record), level="INFO")
    try:
        await require_superadmin(request=_admin_request(), user=admin, session=session)
    finally:
        loguru_logger.remove(sink_id)

    assert len(records) == 1
    extra = records[0]["extra"]
    assert extra["admin_action"] is True
    assert extra["actor_id"] == str(admin.id)
    assert extra["path"] == "/api/v1/admin/orgs/acme/feature-flags"
    assert extra["target"] == {"org_id": "acme"}


async def test_require_superadmin_does_not_log_on_denial(
    active_user: User, session: AsyncSession
) -> None:
    from loguru import logger as loguru_logger  # noqa: PLC0415

    records: list[dict[str, object]] = []
    sink_id = loguru_logger.add(lambda message: records.append(message.record), level="INFO")
    try:
        with pytest.raises(PermissionDeniedError):
            await require_superadmin(request=_admin_request(), user=active_user, session=session)
    finally:
        loguru_logger.remove(sink_id)

    assert records == []
