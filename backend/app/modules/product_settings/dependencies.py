"""
The one ProductSettings switch that is enforced on a route rather than in the
maintenance middleware (T-263).

`signup_disabled` guards exactly one endpoint -- `POST /api/v1/auth/register`
-- and the middleware has no business knowing that. A path literal in a
middleware is a rule nobody finds from the route it governs, and it silently
stops applying the day the route moves; a dependency sits on the route, moves
with it, and shows up in the same place a reader looks for every other gate on
that endpoint.

Not extended to invitation acceptance on purpose: an existing customer adding
a colleague is not "signup", and an operator who closes public registration
during an incident (or a launch) would be surprised to find they had also
frozen their paying customers' teams.
"""

from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.dependencies import get_redis
from app.core.exceptions import DomainError

from .service import is_signup_disabled, read_settings


class SignupDisabledError(DomainError):
    """403 rather than 503: this is not "try again in a minute", it is "this
    product is not accepting new accounts right now", and a client should say
    so rather than retry."""

    status_code = 403
    code = "signup_disabled"
    default_message_key = "errors.signup_disabled"


async def reject_if_signup_disabled(
    redis: Annotated[Redis, Depends(get_redis)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    # The route already depends on `get_session`, and FastAPI caches a
    # dependency per request -- so this is the *same* session the handler
    # uses, not a second connection opened to read one row.
    if is_signup_disabled(await read_settings(redis, session)):
        raise SignupDisabledError(
            fallback="New account registration is currently closed.",
        )
