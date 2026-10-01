"""A `get_session` override for tests that share one session with the app.

The savepoint `session` fixture (tests/conftest.py) hands the test and every
request the *same* AsyncSession. Overriding get_session with
`lambda: session` did that, but a plain function is not a yield dependency,
so get_session's `except Exception: rollback()` never ran under test. A route
that wrote something and then raised kept that write in the test and lost it
in production. That hid review finding R2 (2026-09-29): refresh-token theft
revocation and the login lockout both "worked" in every HTTP test and in no
deployment.

`rolling_back(session)` restores the production contract. On an exception
the session rolls back to its savepoint (the fixture's listener opens a new
one), so the rejected request's writes are gone while data the test seeded
and committed beforehand survives, just as a real request cannot undo
another request's commit.
"""

from collections.abc import AsyncGenerator, Awaitable, Callable

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession


def rolling_back(
    session: AsyncSession,
) -> Callable[[], AsyncGenerator[AsyncSession, None]]:
    async def _get_session() -> AsyncGenerator[AsyncSession, None]:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise

    return _get_session


def reloading[T](
    session: AsyncSession, rows: tuple[T, ...]
) -> Callable[[], Awaitable[tuple[T, ...]]]:
    """A dependency override that hands back `rows`, reloaded per request.

    For overriding get_current_org and friends with fixed rows. Production
    loads them fresh on every request; a plain `lambda: rows` handed over the
    test's own instances, which a rejected request's rollback expires -- and
    the next request then read an expired attribute outside a greenlet.
    """

    async def _reload() -> tuple[T, ...]:
        for row in rows:
            # Only rows this session tracks and a rollback has expired: some
            # tests pass hand-built, never-persisted rows, which need nothing.
            state = inspect(row)
            if state.persistent and state.expired_attributes:
                await session.refresh(row)
        return rows

    return _reload
