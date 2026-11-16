"""Sign-up abuse: Cloudflare Turnstile in front of the three forms that send
an email to a typed-in address, and the disposable-email blocklist.

Turnstile is off without keys, so every other test in the suite signs up
without a token; here it is switched on per test, with Cloudflare's answer
mocked at captcha._new_client.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import module_loader
from app.core.config import get_settings
from app.core.exceptions import UnprocessableEntityError
from app.modules.auth import captcha
from app.modules.auth.email_domains import is_blocked_email
from app.modules.auth.repository import UserRepository
from app.modules.auth.schemas import RegisterRequest
from app.modules.auth.service import AuthService
from app.modules.auth.utils import hash_password

_PASSWORD = "Correct-Horse9"
_GOOD = "token-cloudflare-accepts"


@pytest.fixture
def cloudflare(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    """Turnstile on, and Cloudflare accepting only _GOOD. Returns what was sent."""
    settings = get_settings()
    monkeypatch.setattr(settings, "TURNSTILE_SITE_KEY", "1x00000000000000000000AA")
    monkeypatch.setattr(settings, "TURNSTILE_SECRET_KEY", "test-secret")
    sent: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        form = dict(httpx.QueryParams(request.content.decode()))
        sent.append(form)
        return httpx.Response(200, json={"success": form.get("response") == _GOOD})

    monkeypatch.setattr(
        captcha, "_new_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    return sent


@pytest.fixture
def _no_domain_rules() -> Iterator[None]:
    module_loader.get_email_domain_rules.cache_clear()
    yield
    module_loader.get_email_domain_rules.cache_clear()


# --- Turnstile ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/v1/auth/register", {"email": "new@example.com", "password": _PASSWORD}),
        ("/api/v1/auth/magic-link", {"email": "new@example.com"}),
        ("/api/v1/auth/forgot-password", {"email": "new@example.com"}),
    ],
)
async def test_each_form_needs_a_token_cloudflare_accepts(
    client: httpx.AsyncClient,
    cloudflare: list[dict[str, str]],
    path: str,
    body: dict[str, Any],
) -> None:
    missing = await client.post(path, json=body)
    rejected = await client.post(path, json={**body, "captcha_token": "forged"})
    accepted = await client.post(path, json={**body, "captcha_token": _GOOD})

    assert missing.status_code == 422
    assert missing.json()["error"]["message_key"] == "errors.captcha_required"
    assert rejected.status_code == 422
    assert rejected.json()["error"]["message_key"] == "errors.captcha_failed"
    assert accepted.status_code in (200, 201, 202)
    # The secret goes to Cloudflare, with the token and the caller's address.
    assert cloudflare[-1]["secret"] == "test-secret"
    assert cloudflare[-1]["response"] == _GOOD
    assert cloudflare[-1]["remoteip"]


async def test_off_without_keys(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/auth/magic-link", json={"email": "new@example.com"})

    assert response.status_code == 200


@pytest.mark.usefixtures("cloudflare")
async def test_the_site_key_reaches_the_browser(client: httpx.AsyncClient) -> None:
    on = await client.get("/api/v1/config")

    assert on.json()["data"]["turnstile_site_key"] == "1x00000000000000000000AA"


async def test_no_site_key_when_off(client: httpx.AsyncClient) -> None:
    off = await client.get("/api/v1/config")

    assert off.json()["data"]["turnstile_site_key"] is None


def test_one_key_without_the_other_is_refused_at_startup() -> None:
    settings = get_settings()
    with pytest.raises(RuntimeError, match="TURNSTILE"):
        type(settings)(
            **{**settings.model_dump(), "TURNSTILE_SITE_KEY": "x", "TURNSTILE_SECRET_KEY": ""}
        )


# --- Disposable domains --------------------------------------------------------


@pytest.mark.usefixtures("_no_domain_rules")
def test_disposable_domains_and_their_subdomains_are_blocked() -> None:
    assert is_blocked_email("someone@mailinator.com")
    assert is_blocked_email("someone@inbox.MAILINATOR.com")
    assert not is_blocked_email("someone@gmail.com")
    assert not is_blocked_email("someone@example.com")


@pytest.mark.usefixtures("_no_domain_rules")
def test_the_product_can_block_more_and_allow_some(monkeypatch: pytest.MonkeyPatch) -> None:
    import product.config as product_config  # noqa: PLC0415

    monkeypatch.setattr(
        product_config, "BLOCKED_EMAIL_DOMAINS", ("competitor.test",), raising=False
    )
    monkeypatch.setattr(product_config, "ALLOWED_EMAIL_DOMAINS", ("mailinator.com",), raising=False)

    assert is_blocked_email("spy@sales.competitor.test")
    assert not is_blocked_email("qa@mailinator.com")


@pytest.mark.usefixtures("_no_domain_rules")
def test_a_malformed_product_setting_says_where_to_fix_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import product.config as product_config  # noqa: PLC0415

    monkeypatch.setattr(product_config, "BLOCKED_EMAIL_DOMAINS", "competitor.test", raising=False)

    with pytest.raises(RuntimeError, match=r"product/config\.py"):
        module_loader.get_email_domain_rules()


@pytest.mark.usefixtures("_no_domain_rules")
async def test_sign_up_refuses_a_disposable_address(session: AsyncSession) -> None:
    with pytest.raises(UnprocessableEntityError) as raised:
        await AuthService(session).register(
            RegisterRequest(email=f"{uuid4().hex}@mailinator.com", password=_PASSWORD)
        )

    assert raised.value.message_key == "errors.email_domain_not_allowed"


@pytest.mark.usefixtures("_no_domain_rules")
async def test_email_change_refuses_a_disposable_address(
    session: AsyncSession, redis_client: Redis
) -> None:
    user = await UserRepository(session).create(
        email=f"{uuid4()}@example.com",
        hashed_password=hash_password(_PASSWORD),
        email_verified_at=datetime.now(UTC),
    )

    with pytest.raises(UnprocessableEntityError) as raised:
        await AuthService(session).request_email_change(
            user, "moved@mailinator.com", _PASSWORD, "127.0.0.1", "test", redis_client
        )

    assert raised.value.message_key == "errors.email_domain_not_allowed"


async def test_the_real_client_has_a_timeout() -> None:
    # The seam every other test replaces: a hung Cloudflare must not hang sign-up.
    async with captcha._new_client() as client:
        assert client.timeout.read == captcha._TIMEOUT_SECONDS
