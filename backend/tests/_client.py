"""The one way a test talks to the app over HTTP.

Both database modes -- the savepoint `client` in tests/conftest.py and the
committed one in tests/committed/conftest.py -- serve the app through this,
so what "the app under test" means is written down once.

The line that matters most is `app.state.redis = redis`. The lifespan builds
its own Redis client from REDIS_URL, which under test is `.env.test`'s
address, where nothing listens. The maintenance middleware reads product
settings through that client on every request, before any dependency
override can apply. Twelve private copies of this fixture forgot it, so every
request they made waited on two refused connections (seconds each on
Windows) and quietly took the middleware's fail-open path instead of the real
one. `tests/conftest.py`'s `_no_dead_addresses` now fails any test that
reaches for those addresses at all.
"""

from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx
from httpx import ASGITransport
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.dependencies import get_redis
from app.main import app


@asynccontextmanager
async def serve_app(
    *,
    get_session_override: Callable[[], AsyncGenerator[AsyncSession, None]],
    redis: Redis,
    raise_app_exceptions: bool = True,
) -> AsyncIterator[httpx.AsyncClient]:
    app.dependency_overrides[get_session] = get_session_override
    app.dependency_overrides[get_redis] = lambda: redis
    try:
        async with app.router.lifespan_context(app):
            app.state.redis = redis
            transport = ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)
            # https:// so httpx's cookie jar forwards Secure-marked cookies
            # (the refresh_token cookie) back on later requests.
            async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as c:
                yield c
    finally:
        app.dependency_overrides.clear()
