"""
Verifies T-023 (OAuth -- Google + GitHub): the state-cookie CSRF protection
on the /auth/oauth/{provider} -> {provider}/callback round trip, the
link-existing-account / create-new-account branches of
AuthService.oauth_login(), and the two concrete authlib clients
(GoogleOAuthClient, GitHubOAuthClient) against a mocked HTTP transport --
never a real provider.

Router-level tests fake the OAuthClient dependency entirely via
app.dependency_overrides[get_oauth_clients] -- the same pattern
test_auth_router.py uses for get_redis/get_session -- so the CSRF and
account-linking logic is exercised without any real network calls. The
dedicated Google/GitHub client tests below mock at the httpx transport layer
instead (httpx.MockTransport, passed through GoogleOAuthClient/
GitHubOAuthClient's `transport=` test seam) to cover those two classes' own
authlib plumbing, which the router-level fakes never touch.
"""

from collections.abc import AsyncGenerator, Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from httpx import ASGITransport
from limits.storage import RedisStorage, storage_from_string
from limits.strategies import FixedWindowRateLimiter
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from app.core.config import get_settings
from app.core.db import get_session
from app.core.dependencies import get_redis
from app.core.exceptions import AuthenticationError
from app.main import app
from app.middleware import rate_limit as rate_limit_module
from app.modules.auth.models import OAuthAccount, User
from app.modules.auth.models import Session as SessionModel
from app.modules.auth.oauth import (
    GitHubOAuthClient,
    GoogleOAuthClient,
    OAuthClient,
    get_oauth_clients,
)
from app.modules.auth.repository import OAuthAccountRepository, UserRepository
from app.modules.auth.schemas import OAuthProfile
from app.modules.auth.service import AuthService
from app.modules.auth.utils import create_access_token, hash_password

_TABLES = [User.__table__, SessionModel.__table__, OAuthAccount.__table__]
_VALID_PASSWORD = "Correct-Horse9"


@dataclass
class _FakeOAuthClient:
    """OAuthClient test double -- replaces GoogleOAuthClient/GitHubOAuthClient
    entirely at the router level so tests never touch a real provider."""

    profile: OAuthProfile
    authorize_url: str = "https://fake-provider.example/authorize"
    redirect_uris_seen: list[str] = field(default_factory=list)

    def authorization_url(self, *, redirect_uri: str, state: str) -> str:
        self.redirect_uris_seen.append(redirect_uri)
        return f"{self.authorize_url}?state={state}"

    async def fetch_profile(self, *, code: str, redirect_uri: str) -> OAuthProfile:  # noqa: ARG002
        return self.profile


# --- fixtures: containers, DB session, app client -----------------------------


@pytest.fixture(scope="module")
def postgres_container() -> "PostgresContainer":
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture(scope="module")
def redis_container() -> "RedisContainer":
    with RedisContainer("redis:7-alpine") as container:
        yield container


# Function-scoped: an asyncpg-backed engine can't be reused across
# pytest-asyncio's per-test event loop (see test_auth_repository.py).
@pytest.fixture
async def test_engine(postgres_container: "PostgresContainer") -> AsyncGenerator[AsyncEngine, None]:
    url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all, tables=_TABLES)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all, tables=_TABLES)
    await engine.dispose()


@pytest.fixture
def session_factory(test_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as s:
        yield s


@pytest.fixture
def user_repo(session: AsyncSession) -> UserRepository:
    return UserRepository(session)


@pytest.fixture
def oauth_account_repo(session: AsyncSession) -> OAuthAccountRepository:
    return OAuthAccountRepository(session)


@pytest.fixture
def get_session_override(
    session_factory: async_sessionmaker[AsyncSession],
) -> Callable[[], AsyncGenerator[AsyncSession, None]]:
    # Mirrors app.core.db.get_session: a fresh session per request, so a
    # route's `await session.commit()` is what makes the write visible to the
    # next request -- the same contract as production.
    async def _get_session() -> AsyncGenerator[AsyncSession, None]:
        request_session = session_factory()
        try:
            yield request_session
        except Exception:
            await request_session.rollback()
            raise
        finally:
            await request_session.close()

    return _get_session


@pytest.fixture
async def redis_client(redis_container: "RedisContainer") -> AsyncGenerator[Redis, None]:
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    client: Redis = Redis.from_url(f"redis://{host}:{port}/0")
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture(scope="module")
def _redis_backed_storage(redis_container: "RedisContainer") -> Iterator[RedisStorage]:
    # Same swap-the-singleton's-storage approach as test_rate_limit.py /
    # test_auth_router.py: oauth_start/oauth_callback are both @auth_limit.
    original_storage = rate_limit_module.limiter._storage
    original_limiter = rate_limit_module.limiter._limiter
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    storage = storage_from_string(f"redis://{host}:{port}/1")
    rate_limit_module.limiter._storage = storage
    rate_limit_module.limiter._limiter = FixedWindowRateLimiter(storage)
    yield storage
    rate_limit_module.limiter._storage = original_storage
    rate_limit_module.limiter._limiter = original_limiter


@pytest.fixture
def oauth_clients() -> dict[str, OAuthClient]:
    # Empty by default ("unknown provider") -- individual tests populate
    # whichever provider(s) they need.
    return {}


@pytest.fixture
async def client(
    _redis_backed_storage: RedisStorage,
    get_session_override: Callable[[], AsyncGenerator[AsyncSession, None]],
    redis_client: Redis,
    oauth_clients: dict[str, OAuthClient],
) -> AsyncGenerator[httpx.AsyncClient, None]:
    _redis_backed_storage.reset()
    app.dependency_overrides[get_session] = get_session_override
    app.dependency_overrides[get_redis] = lambda: redis_client
    app.dependency_overrides[get_oauth_clients] = lambda: oauth_clients
    try:
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as c:
                yield c
    finally:
        app.dependency_overrides.clear()


async def _start_oauth_flow(client: httpx.AsyncClient, provider: str = "google") -> str:
    response = await client.get(f"/api/v1/auth/oauth/{provider}")
    assert response.status_code == 302
    state = response.cookies.get("oauth_state")
    assert state is not None
    return state


# --- GET /auth/oauth/{provider} -----------------------------------------------


async def test_oauth_start_unknown_provider_returns_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/auth/oauth/does-not-exist")

    assert response.status_code == 404


async def test_oauth_start_redirects_and_sets_state_cookie(
    client: httpx.AsyncClient, oauth_clients: dict[str, OAuthClient]
) -> None:
    fake = _FakeOAuthClient(
        profile=OAuthProfile(provider_user_id="g-1", email="x@example.com", email_verified=True)
    )
    oauth_clients["google"] = fake

    response = await client.get("/api/v1/auth/oauth/google")

    assert response.status_code == 302
    assert response.headers["location"].startswith(fake.authorize_url)
    cookie = response.headers.get_list("set-cookie")[0]
    assert cookie.startswith("oauth_state=")
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/api/v1/auth/oauth" in cookie
    assert fake.redirect_uris_seen[0].endswith("/api/v1/auth/oauth/google/callback")


# --- GET /auth/oauth/{provider}/callback --------------------------------------


async def test_oauth_callback_unknown_provider_returns_404(client: httpx.AsyncClient) -> None:
    response = await client.get(
        "/api/v1/auth/oauth/does-not-exist/callback", params={"code": "x", "state": "y"}
    )

    assert response.status_code == 404


async def test_oauth_callback_missing_state_cookie_returns_400(
    client: httpx.AsyncClient, oauth_clients: dict[str, OAuthClient]
) -> None:
    oauth_clients["google"] = _FakeOAuthClient(
        profile=OAuthProfile(provider_user_id="g-1", email="x@example.com", email_verified=True)
    )

    response = await client.get(
        "/api/v1/auth/oauth/google/callback", params={"code": "x", "state": "whatever"}
    )

    assert response.status_code == 400
    assert response.json()["data"] is None


async def test_oauth_callback_state_mismatch_returns_400(
    client: httpx.AsyncClient, oauth_clients: dict[str, OAuthClient]
) -> None:
    oauth_clients["google"] = _FakeOAuthClient(
        profile=OAuthProfile(provider_user_id="g-1", email="x@example.com", email_verified=True)
    )
    real_state = await _start_oauth_flow(client)

    response = await client.get(
        "/api/v1/auth/oauth/google/callback",
        params={"code": "x", "state": "not-the-real-state"},
        cookies={"oauth_state": real_state},
    )

    assert response.status_code == 400


async def test_oauth_callback_state_signed_for_a_different_provider_returns_400(
    client: httpx.AsyncClient, oauth_clients: dict[str, OAuthClient]
) -> None:
    # The state cookie's Path (/api/v1/auth/oauth) covers every provider's
    # callback, so a state token signed for "google" is still a *valid*
    # cookie/query match if replayed against "/github/callback" -- it must
    # be rejected on the signed `provider` claim instead.
    oauth_clients["google"] = _FakeOAuthClient(
        profile=OAuthProfile(provider_user_id="g-1", email="x@example.com", email_verified=True)
    )
    oauth_clients["github"] = _FakeOAuthClient(
        profile=OAuthProfile(provider_user_id="gh-1", email="y@example.com", email_verified=True)
    )
    google_state = await _start_oauth_flow(client, provider="google")

    response = await client.get(
        "/api/v1/auth/oauth/github/callback",
        params={"code": "x", "state": google_state},
        cookies={"oauth_state": google_state},
    )

    assert response.status_code == 400


async def test_oauth_callback_new_email_creates_user_with_no_password(
    client: httpx.AsyncClient,
    oauth_clients: dict[str, OAuthClient],
    user_repo: UserRepository,
) -> None:
    oauth_clients["google"] = _FakeOAuthClient(
        profile=OAuthProfile(
            provider_user_id="g-100",
            email="new-oauth@example.com",
            email_verified=True,
            full_name="New User",
        )
    )
    state = await _start_oauth_flow(client)

    response = await client.get(
        "/api/v1/auth/oauth/google/callback",
        params={"code": "x", "state": state},
        cookies={"oauth_state": state},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["access_token"]
    assert data["user"]["email"] == "new-oauth@example.com"
    assert response.headers.get_list("set-cookie")  # refresh_token cookie

    user = await user_repo.get_by_email("new-oauth@example.com")
    assert user is not None
    assert user.hashed_password is None
    assert user.email_verified_at is not None


async def test_oauth_callback_existing_verified_email_links_account(
    client: httpx.AsyncClient,
    oauth_clients: dict[str, OAuthClient],
    user_repo: UserRepository,
    oauth_account_repo: OAuthAccountRepository,
    session: AsyncSession,
) -> None:
    existing = await user_repo.create(
        email="linked@example.com",
        hashed_password=hash_password(_VALID_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    await session.commit()
    oauth_clients["google"] = _FakeOAuthClient(
        profile=OAuthProfile(
            provider_user_id="g-200", email="linked@example.com", email_verified=True
        )
    )
    state = await _start_oauth_flow(client)

    response = await client.get(
        "/api/v1/auth/oauth/google/callback",
        params={"code": "x", "state": state},
        cookies={"oauth_state": state},
    )

    assert response.status_code == 200
    assert response.json()["data"]["user"]["email"] == "linked@example.com"
    linked = await oauth_account_repo.get_by_provider_account("google", "g-200")
    assert linked is not None
    assert linked.user_id == existing.id
    # Still exactly one user for this email -- no duplicate was created, and
    # the original password-based credential is untouched.
    refetched = await user_repo.get_by_email("linked@example.com")
    assert refetched is not None
    assert refetched.id == existing.id
    assert refetched.hashed_password is not None


async def test_oauth_callback_reuses_existing_oauth_account_on_second_login(
    client: httpx.AsyncClient,
    oauth_clients: dict[str, OAuthClient],
    user_repo: UserRepository,
) -> None:
    profile = OAuthProfile(
        provider_user_id="g-300", email="repeat@example.com", email_verified=True
    )
    oauth_clients["google"] = _FakeOAuthClient(profile=profile)
    state1 = await _start_oauth_flow(client)
    first = await client.get(
        "/api/v1/auth/oauth/google/callback",
        params={"code": "x", "state": state1},
        cookies={"oauth_state": state1},
    )
    client.cookies.clear()

    state2 = await _start_oauth_flow(client)
    second = await client.get(
        "/api/v1/auth/oauth/google/callback",
        params={"code": "x", "state": state2},
        cookies={"oauth_state": state2},
    )

    # A second link attempt for the same (provider, provider_user_id) would
    # hit the unique constraint and 500 if oauth_login() failed to recognize
    # the already-linked account -- both calls succeeding is the assertion.
    assert first.status_code == second.status_code == 200
    assert first.json()["data"]["user"]["email"] == second.json()["data"]["user"]["email"]
    user = await user_repo.get_by_email("repeat@example.com")
    assert user is not None


async def test_oauth_callback_unverified_provider_email_returns_401(
    client: httpx.AsyncClient, oauth_clients: dict[str, OAuthClient]
) -> None:
    oauth_clients["google"] = _FakeOAuthClient(
        profile=OAuthProfile(
            provider_user_id="g-400", email="unverified@example.com", email_verified=False
        )
    )
    state = await _start_oauth_flow(client)

    response = await client.get(
        "/api/v1/auth/oauth/google/callback",
        params={"code": "x", "state": state},
        cookies={"oauth_state": state},
    )

    assert response.status_code == 401


async def test_oauth_callback_verifies_previously_unverified_local_user(
    client: httpx.AsyncClient,
    oauth_clients: dict[str, OAuthClient],
    user_repo: UserRepository,
    session: AsyncSession,
) -> None:
    pending = await user_repo.create(
        email="pending@example.com", hashed_password=hash_password(_VALID_PASSWORD)
    )
    await session.commit()
    assert pending.email_verified_at is None
    oauth_clients["google"] = _FakeOAuthClient(
        profile=OAuthProfile(
            provider_user_id="g-500", email="pending@example.com", email_verified=True
        )
    )
    state = await _start_oauth_flow(client)

    response = await client.get(
        "/api/v1/auth/oauth/google/callback",
        params={"code": "x", "state": state},
        cookies={"oauth_state": state},
    )

    assert response.status_code == 200
    # The callback committed the update through a *different* session (the
    # router's request-scoped one) -- refresh, or this session's identity map
    # just hands back the pre-update `pending` object untouched.
    await session.refresh(pending)
    assert pending.email_verified_at is not None
    assert pending.is_active is True


async def test_oauth_callback_sets_refresh_cookie_and_clears_state_cookie(
    client: httpx.AsyncClient, oauth_clients: dict[str, OAuthClient]
) -> None:
    oauth_clients["google"] = _FakeOAuthClient(
        profile=OAuthProfile(
            provider_user_id="g-600", email="cookies@example.com", email_verified=True
        )
    )
    state = await _start_oauth_flow(client)

    response = await client.get(
        "/api/v1/auth/oauth/google/callback",
        params={"code": "x", "state": state},
        cookies={"oauth_state": state},
    )

    cookies = response.headers.get_list("set-cookie")
    refresh_cookie = next(c for c in cookies if c.startswith("__Host-refresh_token="))
    assert "HttpOnly" in refresh_cookie
    state_clear_cookie = next(c for c in cookies if c.startswith("oauth_state="))
    assert "Max-Age=0" in state_clear_cookie


# --- GET /auth/oauth/accounts (T-216) ------------------------------------------


def _auth_header(user: User) -> dict[str, str]:
    token = create_access_token(user.id, None, ())
    return {"Authorization": f"Bearer {token}"}


async def test_list_oauth_accounts_without_bearer_token_returns_401(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/v1/auth/oauth/accounts")

    assert response.status_code == 401


async def test_list_oauth_accounts_returns_linked_providers(
    client: httpx.AsyncClient,
    user_repo: UserRepository,
    oauth_account_repo: OAuthAccountRepository,
    session: AsyncSession,
) -> None:
    user = await user_repo.create(
        email="listed@example.com",
        hashed_password=hash_password(_VALID_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    await oauth_account_repo.create(user_id=user.id, provider="google", provider_user_id="g-list-1")
    await session.commit()

    response = await client.get("/api/v1/auth/oauth/accounts", headers=_auth_header(user))

    assert response.status_code == 200
    providers = [account["provider"] for account in response.json()["data"]]
    assert providers == ["google"]


async def test_list_oauth_accounts_empty_when_none_linked(
    client: httpx.AsyncClient, user_repo: UserRepository, session: AsyncSession
) -> None:
    user = await user_repo.create(
        email="nolinks@example.com",
        hashed_password=hash_password(_VALID_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    await session.commit()

    response = await client.get("/api/v1/auth/oauth/accounts", headers=_auth_header(user))

    assert response.status_code == 200
    assert response.json()["data"] == []


# --- POST /auth/oauth/{provider}/unlink (T-216) --------------------------------


async def test_oauth_unlink_without_bearer_token_returns_401(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/auth/oauth/google/unlink")

    assert response.status_code == 401


async def test_oauth_unlink_removes_linked_account(
    client: httpx.AsyncClient,
    user_repo: UserRepository,
    oauth_account_repo: OAuthAccountRepository,
    session: AsyncSession,
) -> None:
    user = await user_repo.create(
        email="unlink@example.com",
        hashed_password=hash_password(_VALID_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    await oauth_account_repo.create(
        user_id=user.id, provider="google", provider_user_id="g-unlink-1"
    )
    await session.commit()

    response = await client.post("/api/v1/auth/oauth/google/unlink", headers=_auth_header(user))

    assert response.status_code == 200
    remaining = await oauth_account_repo.get_by_provider_account("google", "g-unlink-1")
    assert remaining is None


async def test_oauth_unlink_with_no_linked_account_returns_404(
    client: httpx.AsyncClient, user_repo: UserRepository, session: AsyncSession
) -> None:
    user = await user_repo.create(
        email="nolinked@example.com",
        hashed_password=hash_password(_VALID_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    await session.commit()

    response = await client.post("/api/v1/auth/oauth/google/unlink", headers=_auth_header(user))

    assert response.status_code == 404


async def test_oauth_unlink_sole_auth_method_returns_409(
    client: httpx.AsyncClient,
    user_repo: UserRepository,
    oauth_account_repo: OAuthAccountRepository,
    session: AsyncSession,
) -> None:
    # An OAuth-only user (no password, §T-023) with exactly one linked
    # provider has no other way back in -- unlinking would lock them out.
    user = await user_repo.create(
        email="oauth-only@example.com",
        hashed_password=None,
        email_verified_at=datetime.now(UTC),
    )
    await oauth_account_repo.create(user_id=user.id, provider="google", provider_user_id="g-sole-1")
    await session.commit()

    response = await client.post("/api/v1/auth/oauth/google/unlink", headers=_auth_header(user))

    assert response.status_code == 409


async def test_oauth_unlink_sole_provider_succeeds_when_a_password_is_set(
    client: httpx.AsyncClient,
    user_repo: UserRepository,
    oauth_account_repo: OAuthAccountRepository,
    session: AsyncSession,
) -> None:
    # Same "only one linked provider" shape as the 409 case above, but this
    # user also has a password -- so unlinking still leaves them able to log
    # back in and must be allowed.
    user = await user_repo.create(
        email="has-password@example.com",
        hashed_password=hash_password(_VALID_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    await oauth_account_repo.create(
        user_id=user.id, provider="google", provider_user_id="g-haspw-1"
    )
    await session.commit()

    response = await client.post("/api/v1/auth/oauth/google/unlink", headers=_auth_header(user))

    assert response.status_code == 200


async def test_oauth_unlink_enqueues_security_notification_email(
    client: httpx.AsyncClient,
    user_repo: UserRepository,
    oauth_account_repo: OAuthAccountRepository,
    session: AsyncSession,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    user = await user_repo.create(
        email="unlink-notify@example.com",
        hashed_password=hash_password(_VALID_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    await oauth_account_repo.create(
        user_id=user.id, provider="google", provider_user_id="g-notify-1"
    )
    await session.commit()

    response = await client.post("/api/v1/auth/oauth/google/unlink", headers=_auth_header(user))

    assert response.status_code == 200
    email_jobs = [kwargs for name, kwargs in enqueued_jobs if name == "send_email"]
    assert len(email_jobs) == 1
    assert email_jobs[0]["context"]["event"] == "oauth_unlinked"
    assert email_jobs[0]["context"]["provider"] == "Google"


# --- AuthService.oauth_login() (direct, no HTTP) -------------------------------


async def test_oauth_login_linking_to_existing_user_enqueues_security_notification_email(
    session: AsyncSession,
    user_repo: UserRepository,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    existing = await user_repo.create(
        email="link-notify@example.com",
        hashed_password=hash_password(_VALID_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )

    await AuthService(session).oauth_login(
        "google",
        OAuthProfile(
            provider_user_id="g-link-notify-1",
            email=existing.email,
            email_verified=True,
        ),
        "127.0.0.1",
        "pytest",
    )

    email_jobs = [kwargs for name, kwargs in enqueued_jobs if name == "send_email"]
    assert len(email_jobs) == 1
    assert email_jobs[0]["context"]["event"] == "oauth_linked"
    assert email_jobs[0]["context"]["provider"] == "Google"


async def test_oauth_login_brand_new_user_does_not_enqueue_security_notification_email(
    session: AsyncSession,
    enqueued_jobs: list[tuple[str, dict[str, Any]]],
) -> None:
    # A signup-via-OAuth with no prior account has no "change" for a
    # security-notification email to be about.
    await AuthService(session).oauth_login(
        "google",
        OAuthProfile(
            provider_user_id="g-newuser-1", email="brand-new@example.com", email_verified=True
        ),
        "127.0.0.1",
        "pytest",
    )

    email_jobs = [kwargs for name, kwargs in enqueued_jobs if name == "send_email"]
    assert email_jobs == []


# --- AuthService.oauth_login() (direct, no HTTP) -------------------------------


async def test_oauth_login_with_orphaned_account_raises_authentication_error(
    session: AsyncSession,
    user_repo: UserRepository,
    oauth_account_repo: OAuthAccountRepository,
) -> None:
    # An OAuthAccount can outlive the User it points to being soft-deleted
    # (the FK constraint is satisfied -- the row still exists -- but
    # UserRepository.get_by_id() filters deleted_at IS NULL).
    user = await user_repo.create(
        email="soft-deleted@example.com",
        hashed_password=hash_password(_VALID_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    await oauth_account_repo.create(user_id=user.id, provider="google", provider_user_id="orphan-1")
    await user_repo.update(user.id, deleted_at=datetime.now(UTC))
    await session.commit()

    with pytest.raises(AuthenticationError):
        await AuthService(session).oauth_login(
            "google",
            OAuthProfile(
                provider_user_id="orphan-1",
                email="soft-deleted@example.com",
                email_verified=True,
            ),
            "127.0.0.1",
            "pytest",
        )


# --- get_oauth_clients() (dependency, not the route) --------------------------


def test_get_oauth_clients_empty_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "")
    get_settings.cache_clear()

    assert get_oauth_clients() == {}


def test_get_oauth_clients_includes_configured_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "g-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "g-secret")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "gh-id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "gh-secret")
    get_settings.cache_clear()

    clients = get_oauth_clients()

    assert isinstance(clients["google"], GoogleOAuthClient)
    assert isinstance(clients["github"], GitHubOAuthClient)


# --- GoogleOAuthClient / GitHubOAuthClient (authlib plumbing, mocked HTTP) ----


def test_google_oauth_client_authorization_url_contains_state_and_client_id() -> None:
    client = GoogleOAuthClient("client-id-123", "secret")

    url = client.authorization_url(redirect_uri="https://app.example.com/cb", state="state-abc")

    assert "accounts.google.com" in url
    assert "client_id=client-id-123" in url
    assert "state=state-abc" in url


async def test_google_oauth_client_fetch_profile_happy_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "fake-token", "token_type": "Bearer"})
        return httpx.Response(
            200,
            json={
                "sub": "g-42",
                "email": "google-user@example.com",
                "email_verified": True,
                "name": "Google User",
                "picture": "https://img.example.com/g.png",
            },
        )

    client = GoogleOAuthClient("id", "secret", transport=httpx.MockTransport(handler))

    profile = await client.fetch_profile(
        code="auth-code", redirect_uri="https://app.example.com/callback"
    )

    assert profile.provider_user_id == "g-42"
    assert profile.email == "google-user@example.com"
    assert profile.email_verified is True
    assert profile.full_name == "Google User"
    assert profile.avatar_url == "https://img.example.com/g.png"


async def test_google_oauth_client_fetch_profile_http_error_raises_authentication_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "fake-token", "token_type": "Bearer"})
        return httpx.Response(500)

    client = GoogleOAuthClient("id", "secret", transport=httpx.MockTransport(handler))

    with pytest.raises(AuthenticationError):
        await client.fetch_profile(
            code="auth-code", redirect_uri="https://app.example.com/callback"
        )


async def test_google_oauth_client_fetch_profile_missing_field_raises_authentication_error() -> (
    None
):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "fake-token", "token_type": "Bearer"})
        return httpx.Response(200, json={"email_verified": True})  # no "sub"/"email"

    client = GoogleOAuthClient("id", "secret", transport=httpx.MockTransport(handler))

    with pytest.raises(AuthenticationError):
        await client.fetch_profile(
            code="auth-code", redirect_uri="https://app.example.com/callback"
        )


def test_github_oauth_client_authorization_url_contains_state() -> None:
    client = GitHubOAuthClient("gh-client-id", "secret")

    url = client.authorization_url(redirect_uri="https://app.example.com/cb", state="state-xyz")

    assert "github.com/login/oauth/authorize" in url
    assert "state=state-xyz" in url


async def test_github_oauth_client_fetch_profile_happy_path_picks_primary_email() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "fake-token", "token_type": "bearer"})
        if request.url.path == "/user":
            return httpx.Response(
                200,
                json={
                    "id": 999,
                    "name": "Hub User",
                    "avatar_url": "https://img.example.com/h.png",
                    "email": None,
                },
            )
        return httpx.Response(
            200,
            json=[
                {"email": "secondary@example.com", "primary": False, "verified": True},
                {"email": "primary@example.com", "primary": True, "verified": True},
            ],
        )

    client = GitHubOAuthClient("id", "secret", transport=httpx.MockTransport(handler))

    profile = await client.fetch_profile(
        code="auth-code", redirect_uri="https://app.example.com/callback"
    )

    assert profile.provider_user_id == "999"
    assert profile.email == "primary@example.com"
    assert profile.email_verified is True
    assert profile.full_name == "Hub User"
    assert profile.avatar_url == "https://img.example.com/h.png"


async def test_github_oauth_client_fetch_profile_no_email_raises_authentication_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "fake-token", "token_type": "bearer"})
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 1, "name": "No Email"})
        return httpx.Response(200, json=[])

    client = GitHubOAuthClient("id", "secret", transport=httpx.MockTransport(handler))

    with pytest.raises(AuthenticationError):
        await client.fetch_profile(
            code="auth-code", redirect_uri="https://app.example.com/callback"
        )


async def test_github_oauth_client_fetch_profile_http_error_raises_authentication_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "fake-token", "token_type": "bearer"})
        return httpx.Response(500)

    client = GitHubOAuthClient("id", "secret", transport=httpx.MockTransport(handler))

    with pytest.raises(AuthenticationError):
        await client.fetch_profile(
            code="auth-code", redirect_uri="https://app.example.com/callback"
        )
