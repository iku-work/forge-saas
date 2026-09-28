"""The handle a test module gets instead of its own Postgres container.

Sixty-four test files used to start a `PostgresContainer` of their own,
because each creates and drops its own subset of tables and doing that
against the migrated schema `conftest.engine` builds would break every test
that ran afterwards. The isolation was right; the price was not. At fifteen
to twenty seconds per container it was most of a fifty-two minute suite.

A database gives the same isolation for about a tenth of a second, so
`conftest` now hands out one of those instead, wrapped in this. It exposes
only `get_connection_url`, which is the single method any of those files
ever called on the container they owned -- so the switch left all of their
call sites untouched.

It lives here rather than in `conftest` so test modules can name it in an
annotation without importing `conftest` itself, following the same
convention as `tests._module_tables`.
"""

from __future__ import annotations


class DatabaseHandle:
    """One test module's (or one test's) own database, on the shared server."""

    def __init__(self, url: str) -> None:
        self._url = url

    def get_connection_url(self) -> str:
        """The SQLAlchemy URL, in the same `postgresql+psycopg2://` form
        `PostgresContainer` returns -- callers immediately `.replace` that
        driver with asyncpg, which is the only one installed here."""
        return self._url
