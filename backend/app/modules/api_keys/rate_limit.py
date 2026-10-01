"""
Per-key rate limiting (T-312).

Keyed on the key's id, not on an IP and not on a user. Neither of the others
can work here: a fleet of integrations behind one NAT or one cloud provider
collapses to a single address, and an API key has no user at all -- the
person who minted it is not the caller.

Built on T-223's limiter (`app.middleware.rate_limit`) rather than a second
one, so both kinds of traffic share one Redis backend, one storage
configuration and one thing to point at a different Redis in a test. What is
*not* shared is the identity: `@api_limit` keys on `user:<id>` or the client
address, and this keys on `api_key:<id>`. A session and a key hitting the
same endpoint are therefore counted separately -- which is the property
T-312 asks for, and it falls out of the two keyspaces being disjoint rather
than out of any branching.

**The budget is read at request time, never bound at import.** This is the
repo's recurring trap: middleware and limiters constructed at `app.main`
import time read `backend/.env` before conftest applies `.env.test`, which
bit T-223's rate limiter and `test_middleware.py` both.
`get_settings()` is called inside `budget_for()` for that reason, and
`test_api_key_rate_limit.py::test_the_budget_is_read_at_request_time` is the
regression test that fails if anybody hoists it.
"""

from dataclasses import dataclass
from time import time
from uuid import UUID

from limits import RateLimitItemPerMinute

from app.core.config import get_settings
from app.core.exceptions import RateLimitError
from app.middleware.rate_limit import current_strategy

# The identity namespace. `api_key:` rather than a bare id so this keyspace
# cannot collide with `@api_limit`'s `user:<uuid>` -- two UUIDs are never
# equal, but a namespace makes that a fact about the code rather than about
# collision probability, and it makes a key readable in Redis's SLOWLOG.
IDENTITY_NAMESPACE = "api_key"

# The fallback window length reported when the limiter cannot answer. One
# minute, because that is the only window this module ever configures.
_WINDOW_SECONDS = 60


def identity(key_id: UUID) -> str:
    return f"{IDENTITY_NAMESPACE}:{key_id}"


@dataclass(frozen=True)
class RateLimitVerdict:
    """The answer, plus everything a response has to say about it.

    Returned as a value rather than applied to a response inside this module,
    because the headers belong on the *success* path too: `X-RateLimit-*` on
    a 200 is what lets a client slow down before it is refused, and a limit a
    client can only discover by being refused is a support ticket.
    """

    allowed: bool
    limit: int
    remaining: int
    reset_seconds: int

    def headers(self) -> dict[str, str]:
        headers = {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(max(self.remaining, 0)),
            "X-RateLimit-Reset": str(self.reset_seconds),
        }
        if not self.allowed:
            # RFC 9110 §10.2.3. Seconds rather than an HTTP-date: a client
            # parsing this has no reason to also implement date parsing, and
            # a few seconds of clock skew turns a date into a retry that is
            # refused again on arrival.
            headers["Retry-After"] = str(self.reset_seconds)
        return headers


def budget_for(override: int | None) -> int:
    """The requests-per-minute this key gets.

    Per-key first, the setting second. The override is what lets one
    integration be raised without raising everyone, which is the difference
    between a rate limit an operator can live with and one they turn off.
    """
    if override is not None:
        return override
    return get_settings().API_KEY_RATE_LIMIT_PER_MINUTE


def check(key_id: UUID, override: int | None) -> RateLimitVerdict:
    """Consume one request from `key_id`'s budget.

    Fails *open* on a storage error, like every limit here: the shared
    strategy (FailOpenSlidingWindow) decides that once. (Contrast
    `ApiKeyAuthenticator.touch_used`, which fails *closed* on the same error:
    there the safe side of a broken throttle is not writing.)
    """
    limit = budget_for(override)
    item = RateLimitItemPerMinute(limit)
    strategy = current_strategy()
    allowed = strategy.hit(item, identity(key_id))
    stats = strategy.get_window_stats(item, identity(key_id))
    return RateLimitVerdict(
        allowed=allowed,
        limit=limit,
        remaining=stats.remaining,
        # At least 1: a window that resets within the current second would
        # otherwise tell a client to retry after 0 seconds, which is not a
        # backoff instruction, it is a busy loop.
        reset_seconds=max(int(stats.reset_time - time()), 1),
    )


def raise_for(verdict: RateLimitVerdict) -> None:
    """Turn a denial into the 429 the OpenAPI description documents."""
    if verdict.allowed:
        return
    raise RateLimitError(
        "errors.api_key_rate_limited",
        fallback=(
            f"Rate limit exceeded: {verdict.limit} requests per minute for this API key. "
            f"Retry in {verdict.reset_seconds}s."
        ),
        params={"limit": str(verdict.limit)},
        headers=verdict.headers(),
    )
