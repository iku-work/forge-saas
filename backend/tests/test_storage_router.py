"""
Verifies T-112's HTTP surface: presign/confirm/list/download/delete, with
the storage backend faked (T-111's own wire protocol is covered by
test_storage_backend.py against real MinIO -- this file only verifies
routing, permission/plan gating, cross-tenant isolation, and envelope shape,
same division of concerns as test_billing_router.py faking Stripe).

Same real-Postgres + real-Redis testcontainers / httpx.AsyncClient(
ASGITransport(...)) pattern as test_billing_router.py / test_notifications_router.py.
"""

from collections.abc import AsyncGenerator, Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

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
from testcontainers.redis import RedisContainer

from app.core.db import get_session
from app.core.dependencies import get_redis
from app.main import app
from app.middleware import rate_limit as rate_limit_module
from app.models._mixins import utcnow
from app.modules.audit.models import AuditEvent
from app.modules.auth.models import User
from app.modules.auth.permissions import MemberRole
from app.modules.auth.utils import create_access_token
from app.modules.billing.models import Plan, Subscription
from app.modules.notifications.models import Notification
from app.modules.storage import service as storage_service_module
from app.modules.storage.backend import PresignedUpload
from app.modules.storage.models import StoredFile
from app.modules.tenancy.models import Membership, Organization, OrgFeatureFlag
from tests._database import DatabaseHandle

# T-291: this file needs file_storage enabled -- its routes, its tables, or
# both. CI's module-matrix job deselects it in the combination that turns
# every optional module off.
pytestmark = pytest.mark.module_dependent

_TABLES = [
    User.__table__,
    Plan.__table__,
    Subscription.__table__,
    Organization.__table__,
    Membership.__table__,
    StoredFile.__table__,
    Notification.__table__,
    AuditEvent.__table__,
    OrgFeatureFlag.__table__,
]


@dataclass
class _FakeStorageBackend:
    uploaded: dict[str, dict[str, Any]] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)
    # T-225: content-sniffing needs *some* bytes at confirm time -- real PDF
    # magic bytes by default, matching this file's default content_type.
    content: dict[str, bytes] = field(default_factory=dict)

    async def presign_upload(
        self,
        key: str,
        content_type: str,
        max_bytes: int,  # noqa: ARG002 -- fake ignores the byte cap, real enforcement is T-111's
        expires_in: int = 600,
    ) -> PresignedUpload:
        return PresignedUpload(
            url=f"https://fake.example/{key}",
            fields={"Content-Type": content_type},
            key=key,
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
        )

    async def presign_download(
        self,
        key: str,
        expires_in: int = 3600,  # noqa: ARG002 -- fake doesn't model expiry
        *,
        filename: str | None = None,  # noqa: ARG002 -- fake doesn't model Content-Disposition
    ) -> str:
        return f"https://fake.example/download/{key}"

    async def head(self, key: str) -> dict[str, Any] | None:
        return self.uploaded.get(key)

    async def read_prefix(self, key: str, num_bytes: int) -> bytes:  # noqa: ARG002 -- fake ignores the byte cap
        return self.content.get(key, b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.uploaded.pop(key, None)

    async def exists(self, key: str) -> bool:
        return key in self.uploaded


@pytest.fixture(autouse=True)
def fake_backend(monkeypatch: pytest.MonkeyPatch) -> _FakeStorageBackend:
    backend = _FakeStorageBackend()
    monkeypatch.setattr(storage_service_module, "get_storage_backend", lambda: backend)
    return backend


@pytest.fixture(scope="module")
def redis_container() -> "RedisContainer":
    with RedisContainer("redis:7-alpine") as container:
        yield container


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
def session_factory(test_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as s:
        yield s


@pytest.fixture
def get_session_override(
    session_factory: async_sessionmaker[AsyncSession],
) -> Callable[[], AsyncGenerator[AsyncSession, None]]:
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
async def client(
    _redis_backed_storage: RedisStorage,
    get_session_override: Callable[[], AsyncGenerator[AsyncSession, None]],
    redis_client: Redis,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    _redis_backed_storage.reset()
    app.dependency_overrides[get_session] = get_session_override
    app.dependency_overrides[get_redis] = lambda: redis_client
    try:
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as c:
                yield c
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
async def owner(session: AsyncSession) -> User:
    u = User(email=f"owner-{uuid4()}@example.com")
    session.add(u)
    await session.flush()
    await session.commit()
    return u


@pytest.fixture
async def plan(session: AsyncSession) -> Plan:
    p = Plan(
        name="Pro",
        slug=f"pro-{uuid4().hex[:8]}",
        stripe_price_id_monthly="price_m",
        stripe_price_id_yearly="price_y",
        price_monthly=2900,
        price_yearly=29000,
        features={"storage_gb": 10, "max_upload_mb": 5},
    )
    session.add(p)
    await session.flush()
    await session.commit()
    return p


@pytest.fixture
async def org(session: AsyncSession, owner: User, plan: Plan) -> Organization:
    organization = Organization(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    session.add(organization)
    await session.flush()
    session.add(Membership(user_id=owner.id, org_id=organization.id, role=MemberRole.OWNER))
    session.add(
        Subscription(
            org_id=organization.id,
            plan_id=plan.id,
            stripe_subscription_id=f"sub_{uuid4().hex[:8]}",
            status="active",
            current_period_start=utcnow() - timedelta(days=10),
            current_period_end=utcnow() + timedelta(days=20),
        )
    )
    await session.commit()
    return organization


@pytest.fixture
async def free_org(session: AsyncSession, owner: User) -> Organization:
    # No Subscription row -> resolves to the in-memory _FREE_PLAN. That used
    # to mean "denied": the routes gated on require_feature("storage_gb") and
    # its features dict was empty. It now carries real caps instead, so this
    # org can upload -- within them.
    organization = Organization(name="Freebie", slug=f"freebie-{uuid4().hex[:8]}")
    session.add(organization)
    await session.flush()
    session.add(Membership(user_id=owner.id, org_id=organization.id, role=MemberRole.OWNER))
    await session.commit()
    return organization


async def _add_member(
    session: AsyncSession, org: Organization, *, role: MemberRole = MemberRole.MEMBER
) -> User:
    user = User(email=f"member-{uuid4()}@example.com")
    session.add(user)
    await session.flush()
    session.add(Membership(user_id=user.id, org_id=org.id, role=role))
    await session.commit()
    return user


def _headers(user: User, org: Organization) -> dict[str, str]:
    token = create_access_token(user.id, None, ())
    return {"Authorization": f"Bearer {token}", "X-Org-ID": str(org.id)}


async def _presign(
    client: httpx.AsyncClient,
    user: User,
    org: Organization,
    **overrides: Any,  # noqa: ANN401 -- forwarded straight into a JSON request body
):
    body = {
        "filename": "report.pdf",
        "content_type": "application/pdf",
        "size_bytes": 1024,
        **overrides,
    }
    return await client.post("/api/v1/files/presign", json=body, headers=_headers(user, org))


# --- POST /files/presign ----------------------------------------------------------------


async def test_presign_requires_auth(client: httpx.AsyncClient, org: Organization) -> None:
    response = await client.post(
        "/api/v1/files/presign",
        json={"filename": "f.pdf", "content_type": "application/pdf", "size_bytes": 10},
        headers={"X-Org-ID": str(org.id)},
    )

    assert response.status_code == 401


async def test_presign_member_without_files_manage_returns_403(
    client: httpx.AsyncClient, session: AsyncSession, org: Organization
) -> None:
    member = await _add_member(session, org)

    response = await _presign(client, member, org)

    assert response.status_code == 403


async def test_presign_free_plan_is_allowed_within_its_quota(
    client: httpx.AsyncClient, owner: User, free_org: Organization
) -> None:
    """An org that has never paid can still upload.

    This asserted 402 until the storage routes stopped gating on
    require_feature("storage_gb"). A paywall in front of this module was also
    a paywall in front of the avatar apps/web posts through it, which is not
    a thing to charge for -- and it fired for a lapsed paying customer too,
    not only a free one.
    """
    response = await _presign(client, owner, free_org)

    assert response.status_code == 200


async def test_presign_free_plan_still_enforces_its_per_file_cap(
    client: httpx.AsyncClient, owner: User, free_org: Organization
) -> None:
    """Removing the gate did not remove the limit.

    _FREE_PLAN sets max_upload_mb=10. The teeth matter: with `features={}` a
    quota key is absent, and _check_quota skips an absent key -- so an empty
    free plan would now mean *unlimited*, the opposite of the old bug and a
    worse one.
    """
    response = await _presign(client, owner, free_org, size_bytes=11_000_000)

    assert response.status_code == 402


async def test_presign_disallowed_content_type_returns_422(
    client: httpx.AsyncClient, owner: User, org: Organization
) -> None:
    response = await _presign(client, owner, org, content_type="application/x-executable")

    assert response.status_code == 422


async def test_presign_oversized_file_returns_402(
    client: httpx.AsyncClient, owner: User, org: Organization
) -> None:
    response = await _presign(client, owner, org, size_bytes=10_000_000)

    assert response.status_code == 402


async def test_presign_client_supplied_key_is_ignored(
    client: httpx.AsyncClient, owner: User, org: Organization
) -> None:
    response = await _presign(client, owner, org, key="evil-path/file.jpg")

    assert response.status_code == 200
    key = response.json()["data"]["key"]
    assert key.startswith(f"{org.id}/default/")
    assert "evil-path" not in key


async def test_presign_happy_path_returns_upload_url_and_fields(
    client: httpx.AsyncClient, owner: User, org: Organization
) -> None:
    response = await _presign(client, owner, org)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["upload_url"]
    assert data["fields"]
    assert data["file_id"]


# --- POST /files/{id}/confirm -----------------------------------------------------------


async def test_confirm_requires_files_manage(
    client: httpx.AsyncClient, session: AsyncSession, owner: User, org: Organization
) -> None:
    presigned = (await _presign(client, owner, org)).json()["data"]
    member = await _add_member(session, org)

    response = await client.post(
        f"/api/v1/files/{presigned['file_id']}/confirm", headers=_headers(member, org)
    )

    assert response.status_code == 403


async def test_confirm_cross_tenant_returns_404(
    client: httpx.AsyncClient,
    session: AsyncSession,
    owner: User,
    org: Organization,
    plan: Plan,
) -> None:
    presigned = (await _presign(client, owner, org)).json()["data"]
    other_owner = User(email=f"other-{uuid4()}@example.com")
    session.add(other_owner)
    await session.flush()
    other_org = Organization(name="Other", slug=f"other-{uuid4().hex[:8]}")
    session.add(other_org)
    await session.flush()
    session.add(Membership(user_id=other_owner.id, org_id=other_org.id, role=MemberRole.OWNER))
    session.add(
        Subscription(
            org_id=other_org.id,
            plan_id=plan.id,
            stripe_subscription_id=f"sub_{uuid4().hex[:8]}",
            status="active",
            current_period_start=utcnow() - timedelta(days=10),
            current_period_end=utcnow() + timedelta(days=20),
        )
    )
    await session.commit()

    response = await client.post(
        f"/api/v1/files/{presigned['file_id']}/confirm", headers=_headers(other_owner, other_org)
    )

    assert response.status_code == 404


async def test_confirm_no_upload_returns_422(
    client: httpx.AsyncClient, owner: User, org: Organization
) -> None:
    presigned = (await _presign(client, owner, org)).json()["data"]

    response = await client.post(
        f"/api/v1/files/{presigned['file_id']}/confirm", headers=_headers(owner, org)
    )

    assert response.status_code == 422


async def test_confirm_happy_path_then_idempotent_reconfirm(
    client: httpx.AsyncClient, owner: User, org: Organization, fake_backend: _FakeStorageBackend
) -> None:
    presigned = (await _presign(client, owner, org)).json()["data"]
    fake_backend.uploaded[presigned["key"]] = {"ContentLength": 1024, "ETag": '"abc123"'}

    first = await client.post(
        f"/api/v1/files/{presigned['file_id']}/confirm", headers=_headers(owner, org)
    )
    second = await client.post(
        f"/api/v1/files/{presigned['file_id']}/confirm", headers=_headers(owner, org)
    )

    assert first.status_code == 200
    assert first.json()["data"]["status"] == "ready"
    assert second.status_code == 200
    assert second.json()["data"]["status"] == "ready"


# --- GET /files --------------------------------------------------------------------------


async def test_list_files_requires_files_read(client: httpx.AsyncClient, org: Organization) -> None:
    response = await client.get("/api/v1/files", headers={"X-Org-ID": str(org.id)})

    assert response.status_code == 401


async def test_list_files_member_can_read(
    client: httpx.AsyncClient, session: AsyncSession, owner: User, org: Organization
) -> None:
    await _presign(client, owner, org)
    member = await _add_member(session, org)

    response = await client.get("/api/v1/files", headers=_headers(member, org))

    assert response.status_code == 200
    assert len(response.json()["data"]) == 1


async def test_list_files_filters_by_bucket(
    client: httpx.AsyncClient, owner: User, org: Organization
) -> None:
    await _presign(client, owner, org, bucket="default")
    await _presign(
        client, owner, org, filename="avatar.png", content_type="image/png", bucket="avatars"
    )

    response = await client.get(
        "/api/v1/files", params={"bucket": "avatars"}, headers=_headers(owner, org)
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["bucket"] == "avatars"


async def test_list_files_omits_avatars_by_default(
    client: httpx.AsyncClient, owner: User, org: Organization
) -> None:
    """The Files page is the user's documents, not every object the app has
    stored for them.

    Avatars go through this same module (bucket="avatars"), so with no default
    they listed alongside real uploads -- on one production org, 3 live
    avatars against 2 actual files, which is what the page showed.
    """
    await _presign(client, owner, org, filename="report.pdf", bucket="default")
    await _presign(
        client, owner, org, filename="avatar.png", content_type="image/png", bucket="avatars"
    )

    response = await client.get("/api/v1/files", headers=_headers(owner, org))

    assert response.status_code == 200
    data = response.json()["data"]
    assert [f["filename"] for f in data] == ["report.pdf"]
    assert all(f["bucket"] == "default" for f in data)


async def test_list_files_omits_any_special_purpose_bucket_by_default(
    client: httpx.AsyncClient, owner: User, org: Organization
) -> None:
    """An allowlist, not a deny-list for "avatars".

    The next internal bucket -- exports, thumbnails -- should be invisible
    here without anyone remembering to exclude it.
    """
    await _presign(client, owner, org, filename="report.pdf", bucket="default")
    await _presign(client, owner, org, filename="export.pdf", bucket="exports")

    response = await client.get("/api/v1/files", headers=_headers(owner, org))

    assert response.status_code == 200
    assert [f["filename"] for f in response.json()["data"]] == ["report.pdf"]


# --- GET /files/{id}/download -------------------------------------------------------------


async def test_download_cross_tenant_returns_404(
    client: httpx.AsyncClient, session: AsyncSession, owner: User, org: Organization, plan: Plan
) -> None:
    presigned = (await _presign(client, owner, org)).json()["data"]
    other_owner = User(email=f"other-{uuid4()}@example.com")
    session.add(other_owner)
    await session.flush()
    other_org = Organization(name="Other", slug=f"other-{uuid4().hex[:8]}")
    session.add(other_org)
    await session.flush()
    session.add(Membership(user_id=other_owner.id, org_id=other_org.id, role=MemberRole.OWNER))
    session.add(
        Subscription(
            org_id=other_org.id,
            plan_id=plan.id,
            stripe_subscription_id=f"sub_{uuid4().hex[:8]}",
            status="active",
            current_period_start=utcnow() - timedelta(days=10),
            current_period_end=utcnow() + timedelta(days=20),
        )
    )
    await session.commit()

    response = await client.get(
        f"/api/v1/files/{presigned['file_id']}/download", headers=_headers(other_owner, other_org)
    )

    assert response.status_code == 404


async def _mark_scanned(
    session: AsyncSession, file_id: str, *, status: str = "ready", verdict: str | None = "clean"
) -> None:
    """Put a presigned row into the state a completed scan would leave it in.

    Every download test needs this now. They used to call `_presign` and then
    expect a 200, which is worth stating plainly: the row was still
    `pending` -- nothing had been uploaded to that key at all, let alone
    scanned -- and the endpoint signed it anyway. That is the finding.
    """
    stored = await session.get(StoredFile, UUID(file_id))
    assert stored is not None
    stored.status = status
    stored.virus_scan_result = verdict
    await session.commit()


async def test_download_happy_path(
    client: httpx.AsyncClient, session: AsyncSession, owner: User, org: Organization
) -> None:
    presigned = (await _presign(client, owner, org)).json()["data"]
    await _mark_scanned(session, presigned["file_id"])

    response = await client.get(
        f"/api/v1/files/{presigned['file_id']}/download", headers=_headers(owner, org)
    )

    assert response.status_code == 200
    assert response.json()["data"]["url"]


@pytest.mark.parametrize(
    ("status", "verdict", "code"),
    [
        ("pending", None, "errors.file_not_ready"),
        ("ready", None, "errors.file_scan_pending"),
        ("failed", None, "errors.file_scan_failed"),
        ("infected", "Eicar-Test-Signature", "errors.file_infected"),
    ],
)
async def test_download_is_refused_without_a_clean_verdict(  # noqa: PLR0913 -- parametrised over the fixture set
    client: httpx.AsyncClient,
    session: AsyncSession,
    owner: User,
    org: Organization,
    status: str,
    verdict: str | None,
    code: str,
) -> None:
    """Through HTTP, not the service: the router is where the gate has to hold.

    409 rather than 404 -- the caller has already proved it may see this
    file, so hiding it would turn a transient state into a missing file. The
    distinct message keys are what let the UI tell "try again shortly" apart
    from "this file is gone".
    """
    presigned = (await _presign(client, owner, org)).json()["data"]
    await _mark_scanned(session, presigned["file_id"], status=status, verdict=verdict)

    response = await client.get(
        f"/api/v1/files/{presigned['file_id']}/download", headers=_headers(owner, org)
    )

    assert response.status_code == 409
    assert response.json()["error"]["message_key"] == code


# --- DELETE /files/{id} ---------------------------------------------------------------------


async def test_delete_requires_files_manage(
    client: httpx.AsyncClient, session: AsyncSession, owner: User, org: Organization
) -> None:
    presigned = (await _presign(client, owner, org)).json()["data"]
    member = await _add_member(session, org)

    response = await client.delete(
        f"/api/v1/files/{presigned['file_id']}", headers=_headers(member, org)
    )

    assert response.status_code == 403


async def test_delete_admin_can_delete_then_file_is_gone(
    client: httpx.AsyncClient, session: AsyncSession, owner: User, org: Organization
) -> None:
    presigned = (await _presign(client, owner, org)).json()["data"]
    admin = await _add_member(session, org, role=MemberRole.ADMIN)

    response = await client.delete(
        f"/api/v1/files/{presigned['file_id']}", headers=_headers(admin, org)
    )

    assert response.status_code == 200
    listing = await client.get("/api/v1/files", headers=_headers(owner, org))
    assert listing.json()["data"] == []
