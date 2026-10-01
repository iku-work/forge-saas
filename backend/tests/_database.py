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

from typing import TYPE_CHECKING
from uuid import uuid4

import asyncpg

if TYPE_CHECKING:
    from testcontainers.postgres import PostgresContainer


class DatabaseHandle:
    """One test module's (or one test's) own database, on the shared server."""

    def __init__(self, url: str) -> None:
        self._url = url

    def get_connection_url(self) -> str:
        """The SQLAlchemy URL, in the same `postgresql+psycopg2://` form
        `PostgresContainer` returns -- callers immediately `.replace` that
        driver with asyncpg, which is the only one installed here."""
        return self._url


async def clone_template(container: PostgresContainer, template: str) -> str:
    """A fresh database cloned from `template`; returns its asyncpg URL."""
    base = container.get_connection_url().replace("+psycopg2", "")
    name = f"c{uuid4().hex}"
    conn = await asyncpg.connect(base)
    try:
        # Identifiers, not values: both names are built in this file.
        await conn.execute(f'CREATE DATABASE "{name}" TEMPLATE "{template}"')
    finally:
        await conn.close()
    return base.rsplit("/", 1)[0].replace("postgresql://", "postgresql+asyncpg://") + f"/{name}"


async def drop_database(container: PostgresContainer, url: str) -> None:
    # WITH (FORCE) closes anything the test left open, which is what used to
    # make dropping per-test databases a flake rather than a tidy-up.
    base = container.get_connection_url().replace("+psycopg2", "")
    conn = await asyncpg.connect(base)
    try:
        await conn.execute(f'DROP DATABASE "{url.rsplit("/", 1)[-1]}" WITH (FORCE)')
    finally:
        await conn.close()
