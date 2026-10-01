"""Shared request budgets: one per user, one per organization (review R5).

`@api_limit` and its siblings are *endpoint* limits. slowapi keys each one on
the route as well as the caller, so a user who spreads requests across
endpoints gets 100/minute on every one of them, and nothing at all bounds an
organization's combined traffic. §0.6 asks for both: 100/minute per user
across the whole API, and an org-wide ceiling (5000/hour; decided
2026-09-29, to be confirmed by measurement -- see the plan's D1).

These are charged from dependencies, not middleware, because each needs an
identity the middleware phase does not have:

- the user budget from resolve_user_from_token, once the token has verified;
- the org budget from the membership checks (get_current_org, and tenancy's
  path-keyed get_org_membership), **after** membership is verified. Keyed on
  the raw X-Org-ID header, anyone could spend another organization's budget
  just by naming it.

Unauthenticated routes never reach either, so health checks, `/config` and
the Stripe webhook are exempt by construction. API keys have their own
per-key budget (T-312) and are deliberately not counted against the org's:
an integration running flat out at its own budget must not lock that
organization's people out.

Both budgets use the limiter's shared strategy, which fails *open* on a
storage error -- see FailOpenSlidingWindow in app.middleware.rate_limit.
"""

from __future__ import annotations

from time import time
from typing import TYPE_CHECKING

from limits import parse

from app.core.config import get_settings
from app.core.exceptions import RateLimitError
from app.middleware.rate_limit import current_strategy

if TYPE_CHECKING:
    from uuid import UUID

    from fastapi import Request
    from limits import RateLimitItem

# Distinct from slowapi's own keys, which embed the route. A shared budget
# must not share a counter with any single endpoint's.
USER_NAMESPACE = "budget:user"
ORG_NAMESPACE = "budget:org"


def _charge(request: Request, *, marker: str, identity: str, item: RateLimitItem) -> None:
    """Spend one request from `identity`'s budget, raising 429 when it is gone.

    `marker` makes it once per request: FastAPI dedups a dependency within a
    request, but the same identity can be resolved down two paths (a
    session through get_current_user and through app.core.principal).
    """
    if getattr(request.state, marker, False):
        return
    setattr(request.state, marker, True)

    strategy = current_strategy()
    if strategy.hit(item, identity):
        return
    stats = strategy.get_window_stats(item, identity)

    retry_after = max(int(stats.reset_time - time()), 1)
    raise RateLimitError(
        "errors.rate_limited",
        fallback=f"Rate limit exceeded: {item}. Retry in {retry_after}s.",
        headers={
            "Retry-After": str(retry_after),
            "X-RateLimit-Limit": str(item.amount),
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": str(retry_after),
        },
    )


def charge_user(request: Request, user_id: UUID) -> None:
    # Read at request time, never bound at import (the recurring trap: see
    # app.modules.api_keys.rate_limit's docstring).
    item = parse(get_settings().API_RATE_LIMIT)
    _charge(
        request, marker="_user_budget_charged", identity=f"{USER_NAMESPACE}:{user_id}", item=item
    )


def charge_org(request: Request, org_id: UUID) -> None:
    item = parse(get_settings().ORG_RATE_LIMIT)
    _charge(request, marker="_org_budget_charged", identity=f"{ORG_NAMESPACE}:{org_id}", item=item)
