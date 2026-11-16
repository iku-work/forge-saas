"""Cloudflare Turnstile: the check in front of the three forms that send an
email to whatever address is typed in (sign-up, magic link, forgot password).

Off unless TURNSTILE_SECRET_KEY is set (Settings refuses one key without the
other). On, a request without a token, or with one Cloudflare rejects, is
refused before anything is looked up or sent.

If Cloudflare can't be reached after retries, the request fails rather than
passing unchecked: the "turnstile" breaker opens and these three forms answer
503 until it recovers. Sign-in itself never depends on this.
"""

import httpx

from app.core.config import get_settings
from app.core.exceptions import UnprocessableEntityError
from app.core.resilience import resilient

_SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
_TIMEOUT_SECONDS = 10


def captcha_enabled() -> bool:
    return bool(get_settings().TURNSTILE_SECRET_KEY)


async def require_captcha(token: str | None, ip: str) -> None:
    """Refuse the request unless Turnstile vouches for `token`."""
    if not captcha_enabled():
        return
    if not token:
        raise UnprocessableEntityError(
            "errors.captcha_required", fallback="Complete the check before continuing."
        )
    if not await _verify(token, ip):
        raise UnprocessableEntityError(
            "errors.captcha_failed", fallback="The check didn't pass. Try it again."
        )


def _new_client() -> httpx.AsyncClient:
    """The seam tests patch. Never patch httpx.AsyncClient itself: the test
    suite's own client is one, and its requests would go to the mock too."""
    return httpx.AsyncClient(timeout=_TIMEOUT_SECONDS)


@resilient("turnstile")
async def _verify(token: str, ip: str) -> bool:
    async with _new_client() as client:
        response = await client.post(
            _SITEVERIFY_URL,
            data={
                "secret": get_settings().TURNSTILE_SECRET_KEY,
                "response": token,
                "remoteip": ip,
            },
        )
        response.raise_for_status()
        return bool(response.json().get("success"))
