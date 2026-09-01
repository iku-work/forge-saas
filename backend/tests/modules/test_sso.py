"""
The `enterprise_sso` module: the OIDC flow end to end against a mock IdP, and
every way it must refuse one (T-331).

The IdP here is real in the only way that matters — **it signs with a real
RSA key and publishes a real JWKS** — because the four rejections T-331 names
(`nonce`, `aud`, `exp`, signature) are only meaningful against a validator
that is genuinely verifying. A fake that returns a dict of claims would make
every one of those tests pass by construction.

Two pieces of scaffolding, both the same shape the modules they stand in for
already use:

  * `_MockIdp` serves the discovery document, the JWKS and the token
    endpoint through an `httpx.MockTransport`, which is the seam
    `app.modules.auth.oauth` already carries for exactly this.
  * `enterprise_sso` ships **off**, so `build_router()` does not register
    these routes on the app every other test file talks to. The router is
    stood up in a throwaway FastAPI app here, exactly as T-310's and T-320's
    module tests do, and the table is created by an autouse fixture that
    depends on `engine` alone -- see test_webhooks.py for the deadlock that
    rule exists to avoid.
"""

import json
import socket
import time
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey
from limits.storage import RedisStorage
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlmodel import SQLModel

from app.core import url_guard
from app.core.db import get_session
from app.core.dependencies import get_current_org, get_redis
from app.core.handlers import register_exception_handlers
from app.middleware.rate_limit import register_rate_limiting
from app.modules.audit.models import AuditEvent
from app.modules.auth.models import OAuthAccount, User
from app.modules.auth.permissions import MemberRole
from app.modules.auth.repository import UserRepository
from app.modules.auth.utils import hash_password
from app.modules.tenancy.models import Membership, Organization
from app.modules.tenancy.repository import MembershipRepository, OrganizationRepository

# isort: split
from app.modules.sso import oidc
from app.modules.sso import service as sso_service
from app.modules.sso.models import SsoConnection
from app.modules.sso.repository import SsoConnectionRepository
from app.modules.sso.router import router

_PREFIX = "/api/v1/auth/sso"
_ISSUER = "https://idp.example.test"
_DISCOVERY_URL = f"{_ISSUER}/.well-known/openid-configuration"
_CLIENT_ID = "forge-client"
_DOMAIN = "acme.test"

# The guard resolves every hostname, and `idp.example.test` resolves nowhere.
# Patched at `app.core.url_guard.socket` rather than globally, because these
# tests hold a real Postgres connection -- see test_webhooks.py for the
# WinError 10049 that a global patch produces.
_FAKE_DNS = {"idp.example.test": "93.184.216.34"}


def _fake_getaddrinfo(host: str, *_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
    if host not in _FAKE_DNS:
        raise socket.gaierror(socket.EAI_NONAME, "fake DNS: no such host")
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_FAKE_DNS[host], 0))]


@pytest.fixture(autouse=True)
def _fake_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        url_guard,
        "socket",
        SimpleNamespace(getaddrinfo=_fake_getaddrinfo, gaierror=socket.gaierror),
    )


@pytest.fixture(autouse=True)
async def _sso_table(engine: AsyncEngine) -> None:
    # Autouse and on `engine` alone: `CREATE TABLE ... REFERENCES
    # organization(id)` takes a lock that conflicts with the `session`
    # fixture's still-open transaction once a test has inserted an org, and
    # creating it lazily hangs forever with no output (T-310's deadlock).
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all, tables=[SsoConnection.__table__])


class _MockIdp:
    """An identity provider that actually signs things.

    `claims_override` is how a test asks for a *specific* malformed token
    without reaching into the validator: the IdP mints it, and the code under
    test rejects it for the reason a real IdP's mistake would produce.
    """

    def __init__(self) -> None:
        self.key = RSAKey.generate_key(2048, parameters={"kid": "idp-1"})
        self.jwks = KeySet([self.key]).as_dict(private=False)
        self.claims_override: dict[str, Any] = {}
        self.sign_with: RSAKey | None = None
        self.token_endpoint_body: dict[str, Any] | None = None
        self.nonce: str | None = None
        self.discovery = {
            "issuer": _ISSUER,
            "authorization_endpoint": f"{_ISSUER}/authorize",
            "token_endpoint": f"{_ISSUER}/token",
            "jwks_uri": f"{_ISSUER}/jwks",
        }

    def id_token(self, **extra: Any) -> str:  # noqa: ANN401 -- arbitrary claims, on purpose
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": _ISSUER,
            "aud": _CLIENT_ID,
            "sub": "idp-subject-1",
            "exp": now + 300,
            "iat": now,
            "email": f"newcomer@{_DOMAIN}",
            "name": "New Comer",
        }
        if self.nonce is not None:
            claims["nonce"] = self.nonce
        claims.update(extra)
        claims.update(self.claims_override)
        signing_key = self.sign_with or self.key
        return jwt.encode({"alg": "RS256", "kid": signing_key.kid}, claims, signing_key)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("openid-configuration"):
            return httpx.Response(200, json=self.discovery)
        if path.endswith("/jwks"):
            return httpx.Response(200, json=self.jwks)
        if path.endswith("/token"):
            body = self.token_endpoint_body
            if body is None:
                body = {
                    "access_token": "at",
                    "token_type": "Bearer",
                    "id_token": self.id_token(),
                }
            return httpx.Response(200, json=body)
        return httpx.Response(404)  # pragma: no cover -- an unexpected call


@pytest.fixture
def idp(monkeypatch: pytest.MonkeyPatch) -> _MockIdp:
    """A signing IdP, wired into both places this module reaches the network:
    `discovery`'s plain httpx client and `oidc`'s authlib one."""
    mock = _MockIdp()
    transport = httpx.MockTransport(mock.handler)

    from app.modules.sso import discovery as discovery_module  # noqa: PLC0415

    # `discovery.new_client`, not `httpx.AsyncClient`. Patching the class
    # itself also captures this file's own ASGI client and quietly sends
    # every request in the test to the mock IdP -- which shows up as an empty
    # 404 from a route that plainly exists, and costs half an hour.
    monkeypatch.setattr(
        discovery_module, "new_client", lambda: httpx.AsyncClient(transport=transport)
    )

    real_exchange = oidc.exchange_code

    async def _exchange(*args: Any, **kwargs: Any) -> str:  # noqa: ANN401 -- passthrough
        kwargs["transport"] = transport
        return await real_exchange(*args, **kwargs)

    monkeypatch.setattr(oidc, "exchange_code", _exchange)
    return mock


async def _make_org(
    session: AsyncSession, role: MemberRole = MemberRole.OWNER
) -> tuple[Organization, Membership]:
    user = await UserRepository(session).create(
        email=f"{uuid4()}@example.com",
        hashed_password=hash_password("Correct-Horse9"),
        email_verified_at=datetime.now(UTC),
    )
    org = await OrganizationRepository(session).create(name="Acme", slug=f"acme-{uuid4().hex[:8]}")
    membership = await MembershipRepository(session).create(
        user_id=user.id, org_id=org.id, role=role
    )
    await session.flush()
    return org, membership


async def _connect(session: AsyncSession, org: Organization, **overrides: object) -> SsoConnection:
    fields: dict[str, Any] = {
        "provider_label": "Acme IdP",
        "issuer": _ISSUER,
        "discovery_url": _DISCOVERY_URL,
        "client_id": _CLIENT_ID,
        "client_secret": "idp-client-secret",
        "email_domain": _DOMAIN,
        "jit_provisioning": True,
        "default_role": MemberRole.MEMBER,
        "group_role_mappings": {},
        "enforced": False,
        "domain_hint_enabled": False,
        "enabled": True,
    }
    fields.update(overrides)
    connection = await SsoConnectionRepository(session, org.id).create(**fields)
    await session.flush()
    return connection


ClientFactory = Callable[..., httpx.AsyncClient]


@pytest.fixture
async def api_client(
    session: AsyncSession,
    _redis_client: Redis,
    _rate_limit_storage: RedisStorage,
) -> AsyncIterator[ClientFactory]:
    _rate_limit_storage.reset()
    clients: list[httpx.AsyncClient] = []

    def _make(caller: tuple[Organization, Membership] | None = None) -> httpx.AsyncClient:
        api = FastAPI()
        register_exception_handlers(api)
        register_rate_limiting(api)
        api.include_router(router, prefix="/api/v1")
        api.dependency_overrides[get_session] = lambda: session
        api.dependency_overrides[get_redis] = lambda: _redis_client
        if caller is not None:
            api.dependency_overrides[get_current_org] = lambda: caller
        client = httpx.AsyncClient(
            transport=ASGITransport(app=api),
            base_url="https://testserver",
            follow_redirects=False,
        )
        clients.append(client)
        return client

    yield _make

    for client in clients:
        await client.aclose()


async def _start(client: httpx.AsyncClient, slug: str) -> tuple[str, str]:
    """Drives `/start` and returns `(state, nonce)` the way a browser would."""
    response = await client.get(f"{_PREFIX}/{slug}/start")
    assert response.status_code == 302, response.text
    location = httpx.URL(response.headers["location"])
    state = location.params["state"]
    nonce = location.params["nonce"]
    client.cookies.set("sso_state", state, domain="testserver", path=_PREFIX)
    return state, nonce


# --- the happy path ----------------------------------------------------------


async def test_a_new_person_is_provisioned_with_the_default_role(
    session: AsyncSession, api_client: ClientFactory, idp: _MockIdp
) -> None:
    org, _ = await _make_org(session)
    await _connect(session, org)
    client = api_client()

    _state, nonce = await _start(client, org.slug)
    idp.nonce = nonce
    response = await client.get(f"{_PREFIX}/callback", params={"code": "c", "state": _state})

    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["access_token"]
    assert body["user"]["email"] == f"newcomer@{_DOMAIN}"

    user = await UserRepository(session).get_by_email(f"newcomer@{_DOMAIN}")
    assert user is not None
    # No password, ever: a JIT-provisioned account whose only credential is
    # the IdP is what enforcement assumes.
    assert user.hashed_password is None
    membership = await MembershipRepository(session).get_by_user_and_org(user.id, org.id)
    assert membership is not None
    assert membership.role is MemberRole.MEMBER


async def test_an_existing_person_is_linked_rather_than_duplicated(
    session: AsyncSession, api_client: ClientFactory, idp: _MockIdp
) -> None:
    org, _ = await _make_org(session)
    await _connect(session, org)
    existing = await UserRepository(session).create(
        email=f"newcomer@{_DOMAIN}",
        hashed_password=hash_password("Correct-Horse9"),
        email_verified_at=datetime.now(UTC),
    )
    await session.flush()
    client = api_client()

    state, nonce = await _start(client, org.slug)
    idp.nonce = nonce
    response = await client.get(f"{_PREFIX}/callback", params={"code": "c", "state": state})

    assert response.status_code == 200, response.text
    assert response.json()["data"]["user"]["id"] == str(existing.id)
    users = (
        (await session.execute(select(User).where(User.email == f"newcomer@{_DOMAIN}")))
        .scalars()
        .all()
    )
    assert len(users) == 1
    # The link is scoped to the connection, so the same person at a second
    # organization is a second link rather than a shared one.
    links = (await session.execute(select(OAuthAccount))).scalars().all()
    assert [link.provider for link in links] == [
        sso_service.SSO_PROVIDER_PREFIX + str((await _only_connection(session)).id)
    ]


async def _only_connection(session: AsyncSession) -> SsoConnection:
    return (await session.execute(select(SsoConnection))).scalars().one()


async def test_a_group_mapping_beats_the_default_role(
    session: AsyncSession, api_client: ClientFactory, idp: _MockIdp
) -> None:
    org, _ = await _make_org(session)
    await _connect(
        session,
        org,
        group_claim="groups",
        group_role_mappings={"platform-admins": MemberRole.ADMIN.value},
    )
    client = api_client()

    state, nonce = await _start(client, org.slug)
    idp.nonce = nonce
    idp.claims_override = {"groups": ["everyone", "platform-admins"]}
    response = await client.get(f"{_PREFIX}/callback", params={"code": "c", "state": state})

    assert response.status_code == 200, response.text
    user = await UserRepository(session).get_by_email(f"newcomer@{_DOMAIN}")
    assert user is not None
    membership = await MembershipRepository(session).get_by_user_and_org(user.id, org.id)
    assert membership is not None
    assert membership.role is MemberRole.ADMIN


# --- the four rejections T-331 names -----------------------------------------


async def test_a_wrong_nonce_is_refused(
    session: AsyncSession, api_client: ClientFactory, idp: _MockIdp
) -> None:
    """The check that makes a token specific to *this* attempt."""
    org, _ = await _make_org(session)
    await _connect(session, org)
    client = api_client()

    state, _nonce = await _start(client, org.slug)
    idp.nonce = "a-nonce-from-somebody-elses-login"
    response = await client.get(f"{_PREFIX}/callback", params={"code": "c", "state": state})

    assert response.status_code == 401
    assert response.json()["error"]["message_key"] == "errors.sso_bad_nonce"


async def test_a_wrong_audience_is_refused(
    session: AsyncSession, api_client: ClientFactory, idp: _MockIdp
) -> None:
    """A token minted for another relying party at the same IdP."""
    org, _ = await _make_org(session)
    await _connect(session, org)
    client = api_client()

    state, nonce = await _start(client, org.slug)
    idp.nonce = nonce
    idp.claims_override = {"aud": "somebody-elses-client"}
    response = await client.get(f"{_PREFIX}/callback", params={"code": "c", "state": state})

    assert response.status_code == 401
    assert response.json()["error"]["message_key"] == "errors.sso_bad_audience"


async def test_an_expired_token_is_refused(
    session: AsyncSession, api_client: ClientFactory, idp: _MockIdp
) -> None:
    org, _ = await _make_org(session)
    await _connect(session, org)
    client = api_client()

    state, nonce = await _start(client, org.slug)
    idp.nonce = nonce
    # Past the expiry *and* past the clock-skew tolerance -- otherwise this
    # would be asserting that the skew allowance works, not that expiry does.
    idp.claims_override = {"exp": int(time.time()) - oidc.CLOCK_SKEW_SECONDS - 60}
    response = await client.get(f"{_PREFIX}/callback", params={"code": "c", "state": state})

    assert response.status_code == 401
    assert response.json()["error"]["message_key"] == "errors.sso_expired"


async def test_a_token_signed_by_the_wrong_key_is_refused(
    session: AsyncSession, api_client: ClientFactory, idp: _MockIdp
) -> None:
    """A real signature check, against a real key that is not the IdP's."""
    org, _ = await _make_org(session)
    await _connect(session, org)
    client = api_client()

    state, nonce = await _start(client, org.slug)
    idp.nonce = nonce
    idp.sign_with = RSAKey.generate_key(2048, parameters={"kid": "idp-1"})
    response = await client.get(f"{_PREFIX}/callback", params={"code": "c", "state": state})

    assert response.status_code == 401
    assert response.json()["error"]["message_key"] == "errors.sso_bad_signature"


async def test_a_wrong_issuer_is_refused(
    session: AsyncSession, api_client: ClientFactory, idp: _MockIdp
) -> None:
    org, _ = await _make_org(session)
    await _connect(session, org)
    client = api_client()

    state, nonce = await _start(client, org.slug)
    idp.nonce = nonce
    idp.claims_override = {"iss": "https://not-the-configured-issuer.test"}
    response = await client.get(f"{_PREFIX}/callback", params={"code": "c", "state": state})

    assert response.status_code == 401
    assert response.json()["error"]["message_key"] == "errors.sso_bad_issuer"


# --- the check that makes per-org SSO safe -----------------------------------


async def test_an_idp_cannot_assert_an_identity_outside_its_own_domain(
    session: AsyncSession, api_client: ClientFactory, idp: _MockIdp
) -> None:
    """The whole reason `email_domain` is a required column.

    An Acme admin configures Acme's IdP. Nothing stops that IdP from
    *claiming* to be somebody at another company -- except this.
    """
    org, _ = await _make_org(session)
    await _connect(session, org)
    victim = await UserRepository(session).create(
        email="ceo@rival.test",
        hashed_password=hash_password("Correct-Horse9"),
        email_verified_at=datetime.now(UTC),
    )
    await session.flush()
    client = api_client()

    state, nonce = await _start(client, org.slug)
    idp.nonce = nonce
    idp.claims_override = {"email": "ceo@rival.test"}
    response = await client.get(f"{_PREFIX}/callback", params={"code": "c", "state": state})

    assert response.status_code == 401
    assert response.json()["error"]["message_key"] == "errors.sso_foreign_domain"
    # And nothing was linked to the account it tried to take.
    links = (
        (await session.execute(select(OAuthAccount).where(OAuthAccount.user_id == victim.id)))
        .scalars()
        .all()
    )
    assert links == []


# --- CSRF, state and the module toggle ---------------------------------------


async def test_a_callback_without_the_state_cookie_is_refused(
    session: AsyncSession, api_client: ClientFactory, idp: _MockIdp
) -> None:
    """Login CSRF: without this an attacker completes *their* IdP login in a
    victim's browser and leaves the victim signed in as them."""
    org, _ = await _make_org(session)
    await _connect(session, org)
    client = api_client()

    state, nonce = await _start(client, org.slug)
    idp.nonce = nonce
    client.cookies.clear()
    response = await client.get(f"{_PREFIX}/callback", params={"code": "c", "state": state})

    assert response.status_code == 400
    assert response.json()["error"]["message_key"] == "errors.sso_state_mismatch"


@pytest.mark.usefixtures("idp")
async def test_the_state_cookie_is_set_to_survive_the_hop_back(
    session: AsyncSession, api_client: ClientFactory
) -> None:
    """The attributes on this one cookie decide whether SSO works at all.

    T-220 lost a session to exactly this shape of cross-origin hop, and the
    failure is silent from the server's side: the IdP's redirect back to
    `/callback` is a cross-site top-level navigation, so `SameSite=Strict`
    would drop the cookie and every login would fail the state check with a
    message about CSRF. Asserted attribute by attribute rather than through a
    round trip, because a round trip in-process never exercises a browser's
    SameSite rules and would pass either way.
    """
    org, _ = await _make_org(session)
    await _connect(session, org)
    client = api_client()

    response = await client.get(f"{_PREFIX}/{org.slug}/start")

    assert response.status_code == 302
    header = response.headers["set-cookie"]
    assert header.startswith("sso_state=")
    directives = {part.strip().lower() for part in header.split(";")[1:]}
    assert "samesite=lax" in directives
    assert "httponly" in directives
    assert "secure" in directives
    assert f"path={_PREFIX}" in directives
    # Scoped to the login it belongs to: a state cookie left on `/` would ride
    # along on every request the app makes for the next ten minutes.
    assert f"max-age={sso_service.STATE_TTL_SECONDS}" in directives


async def test_the_callback_clears_the_state_cookie_and_leaves_a_session(
    session: AsyncSession, api_client: ClientFactory, idp: _MockIdp
) -> None:
    """The other half of the redirect chain: the state cookie is spent, and
    the refresh cookie that replaces it is the session surviving the hop."""
    org, _ = await _make_org(session)
    await _connect(session, org)
    client = api_client()

    state, nonce = await _start(client, org.slug)
    idp.nonce = nonce
    response = await client.get(f"{_PREFIX}/callback", params={"code": "c", "state": state})

    assert response.status_code == 200, response.text
    cookies = response.headers.get_list("set-cookie")
    assert any(
        header.startswith("sso_state=") and "max-age=0" in header.lower() for header in cookies
    ), cookies
    # `__Host-` (auth/router.py's `_REFRESH_COOKIE_NAME`), which the prefix
    # itself makes conditional on Secure + Path=/ + no Domain -- a browser
    # rejects the cookie outright if any of those is missing, so the name is
    # load-bearing rather than decorative.
    refresh = next(
        (header for header in cookies if header.startswith("__Host-refresh_token=")),
        None,
    )
    assert refresh is not None, cookies
    directives = {part.strip().lower() for part in refresh.split(";")[1:]}
    assert "httponly" in directives
    assert "secure" in directives


async def test_start_is_a_404_for_an_unknown_or_unconnected_org(
    session: AsyncSession, api_client: ClientFactory
) -> None:
    """Both cases answer the same, so a stranger cannot enumerate
    organizations by slug."""
    org, _ = await _make_org(session)  # no connection
    client = api_client()

    assert (await client.get(f"{_PREFIX}/{org.slug}/start")).status_code == 404
    assert (await client.get(f"{_PREFIX}/no-such-org/start")).status_code == 404


async def test_a_disabled_connection_cannot_start_a_login(
    session: AsyncSession, api_client: ClientFactory
) -> None:
    org, _ = await _make_org(session)
    await _connect(session, org, enabled=False)
    client = api_client()

    assert (await client.get(f"{_PREFIX}/{org.slug}/start")).status_code == 404


# --- the domain hint ---------------------------------------------------------


async def test_the_hint_says_nothing_unless_the_org_opted_in(
    session: AsyncSession, api_client: ClientFactory
) -> None:
    """A domain with SSO and hinting off is indistinguishable from a domain
    with no account here at all -- that is what makes the flag meaningful."""
    org, _ = await _make_org(session)
    await _connect(session, org, domain_hint_enabled=False)
    client = api_client()

    response = await client.post(f"{_PREFIX}/hint", json={"email": f"someone@{_DOMAIN}"})

    assert response.status_code == 200
    assert response.json()["data"] == {
        "sso_available": False,
        "org_slug": None,
        "provider_label": None,
    }


async def test_the_hint_names_the_provider_once_the_org_opts_in(
    session: AsyncSession, api_client: ClientFactory
) -> None:
    org, _ = await _make_org(session)
    await _connect(session, org, domain_hint_enabled=True)
    client = api_client()

    response = await client.post(f"{_PREFIX}/hint", json={"email": f"SOMEONE@{_DOMAIN.upper()}"})

    data = response.json()["data"]
    assert data["sso_available"] is True
    assert data["org_slug"] == org.slug
    assert data["provider_label"] == "Acme IdP"


# --- the secret --------------------------------------------------------------


async def test_the_client_secret_is_encrypted_at_rest_and_absent_from_repr(
    session: AsyncSession,
) -> None:
    org, _ = await _make_org(session)
    connection = await _connect(session, org)
    await session.commit()

    # Raw SQL: going through SQLAlchemy would decrypt on the way out and the
    # assertion would compare the plaintext with itself.
    raw = (
        await session.execute(
            text("SELECT client_secret FROM sso_connection WHERE id = :id"),
            {"id": connection.id},
        )
    ).scalar_one()
    assert raw != connection.client_secret
    assert raw.startswith("gAAAAA")

    rendered = repr(connection)
    assert connection.client_secret not in rendered
    assert "[REDACTED]" in rendered


async def test_no_response_or_audit_diff_carries_the_client_secret(
    session: AsyncSession,
    api_client: ClientFactory,
    idp: _MockIdp,  # noqa: ARG001 -- IdP serves discovery
) -> None:
    caller = await _make_org(session)
    _org, membership = caller
    client = api_client(caller)

    created = await client.put(
        f"{_PREFIX}/connection",
        json={
            "provider_label": "Acme IdP",
            "issuer": _ISSUER,
            "discovery_url": _DISCOVERY_URL,
            "client_id": _CLIENT_ID,
            "client_secret": "super-secret-value",
            "email_domain": _DOMAIN,
        },
    )

    assert created.status_code == 200, created.text
    assert "super-secret-value" not in created.text

    fetched = await client.get(f"{_PREFIX}/connection")
    assert "super-secret-value" not in fetched.text

    events = (
        (await session.execute(select(AuditEvent).where(AuditEvent.org_id == membership.org_id)))
        .scalars()
        .all()
    )
    assert [event.action for event in events] == ["sso_connection.created"]
    assert "super-secret-value" not in json.dumps(events[0].diff)


# --- the admin surface -------------------------------------------------------


async def test_saving_a_connection_whose_issuer_disagrees_is_refused(
    session: AsyncSession, api_client: ClientFactory, idp: _MockIdp
) -> None:
    """The single most common SSO misconfiguration, caught at save time
    rather than by every member at once at the next sign-in."""
    caller = await _make_org(session)
    idp.discovery = {**idp.discovery, "issuer": "https://actually-this.test"}
    client = api_client(caller)

    response = await client.put(
        f"{_PREFIX}/connection",
        json={
            "provider_label": "Acme IdP",
            "issuer": _ISSUER,
            "discovery_url": _DISCOVERY_URL,
            "client_id": _CLIENT_ID,
            "client_secret": "s",
            "email_domain": _DOMAIN,
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["message_key"] == "errors.sso_issuer_mismatch"


async def test_owner_is_refused_as_a_jit_default_role(
    session: AsyncSession,
    api_client: ClientFactory,
    idp: _MockIdp,  # noqa: ARG001
) -> None:
    """An IdP says who somebody is, not what they may do here."""
    caller = await _make_org(session)
    client = api_client(caller)

    response = await client.put(
        f"{_PREFIX}/connection",
        json={
            "provider_label": "Acme IdP",
            "issuer": _ISSUER,
            "discovery_url": _DISCOVERY_URL,
            "client_id": _CLIENT_ID,
            "client_secret": "s",
            "email_domain": _DOMAIN,
            "default_role": "owner",
        },
    )

    assert response.status_code == 422


# --- §0.8 non-negotiable 1: cross-tenant isolation ---------------------------


@pytest.mark.usefixtures("idp")
async def test_one_org_cannot_read_or_overwrite_anothers_connection(
    session: AsyncSession, api_client: ClientFactory
) -> None:
    """The org-scoping probe §0.2 asks for, at the layer that decides it.

    A connection carries no id in its URL -- there is one per organization
    and the route resolves it from the caller's organization -- so what is
    under test is `SsoConnectionRepository`'s scoping rather than a guessable
    identifier. This file's `api_client` overrides `get_current_org`, so the
    caller *is* org B by construction; whether a member of B may claim B at
    all is that dependency's own question, answered centrally in
    tests/security/test_tenant_isolation.py.
    """
    org_a, _ = await _make_org(session)
    caller_b = await _make_org(session)
    await _connect(session, org_a, provider_label="A's IdP")
    client_b = api_client(caller_b)

    read = await client_b.get(f"{_PREFIX}/connection")
    assert read.status_code == 200
    # "You have none" is the honest answer, and it is the same answer whether
    # or not somebody else has one.
    assert read.json()["data"] is None

    written = await client_b.put(
        f"{_PREFIX}/connection",
        json={
            "provider_label": "B's IdP",
            "issuer": _ISSUER,
            "discovery_url": _DISCOVERY_URL,
            "client_id": _CLIENT_ID,
            "client_secret": "s",
            "email_domain": _DOMAIN,
        },
    )
    assert written.status_code == 200, written.text

    # Two rows, and org A's is untouched: the UNIQUE(org_id) that makes "the
    # connection" singular is per organization, not global -- a PUT from B
    # creates B's, it does not overwrite the one next door.
    connections = (await session.execute(select(SsoConnection))).scalars().all()
    assert len(connections) == 2
    a_row = next(c for c in connections if c.org_id == org_a.id)
    assert a_row.provider_label == "A's IdP"

    # And the delete stays on its own side of the fence. Soft, so both rows
    # are still on the table -- what has to be true is that the one B retired
    # is B's.
    assert (await client_b.delete(f"{_PREFIX}/connection")).status_code == 200
    live = (
        (await session.execute(select(SsoConnection).where(SsoConnection.deleted_at.is_(None))))
        .scalars()
        .all()
    )
    assert [c.org_id for c in live] == [org_a.id]


# --- §0.8 non-negotiable 2: the RBAC gate ------------------------------------


async def test_a_plain_member_cannot_read_or_change_the_connection(
    session: AsyncSession, api_client: ClientFactory
) -> None:
    caller = await _make_org(session, MemberRole.MEMBER)
    client = api_client(caller)

    assert (await client.get(f"{_PREFIX}/connection")).status_code == 403
    assert (await client.delete(f"{_PREFIX}/connection")).status_code == 403


async def test_an_enforced_connection_cannot_be_deleted(
    session: AsyncSession, api_client: ClientFactory
) -> None:
    """Enforcement outlives the connection for as long as the delete takes,
    and a member with no password has nothing left."""
    caller = await _make_org(session)
    org, _ = caller
    await _connect(session, org, enforced=True)
    client = api_client(caller)

    response = await client.delete(f"{_PREFIX}/connection")

    assert response.status_code == 409
    assert response.json()["error"]["message_key"] == "errors.sso_delete_while_enforced"


async def test_an_unsafe_discovery_url_is_refused(
    session: AsyncSession, api_client: ClientFactory
) -> None:
    """ "Connect an IdP" is an SSRF primitive without T-212's guard: the URL
    is operator-supplied and this server fetches it."""
    caller = await _make_org(session)
    client = api_client(caller)

    response = await client.put(
        f"{_PREFIX}/connection",
        json={
            "provider_label": "Acme IdP",
            "issuer": _ISSUER,
            "discovery_url": "http://169.254.169.254/latest/meta-data/",
            "client_id": _CLIENT_ID,
            "client_secret": "s",
            "email_domain": _DOMAIN,
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["message_key"] == "errors.sso_unsafe_url"
