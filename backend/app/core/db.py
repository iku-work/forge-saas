from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any
from uuid import uuid4

from fastapi import Depends
from loguru import logger
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.sql import ClauseElement
from sqlalchemy.sql.util import find_tables

from app.core.config import Settings, get_settings

_settings = get_settings()

_echo = _settings.DB_ECHO if _settings.DB_ECHO is not None else _settings.ENVIRONMENT == "dev"


def _unique_prepared_statement_name() -> str:
    return f"__asyncpg_{uuid4()}__"


def build_connect_args(settings: Settings) -> dict[str, Any]:
    """asyncpg driver arguments for this deployment's connection topology.

    Empty (asyncpg's own defaults) unless PGBOUNCER_ENABLED — every key below
    exists solely to survive a transaction-mode pooler, and each one costs
    something, so none of them are paid for by a deployment talking straight
    to Postgres. See docs/adr/0002-pgbouncer.md for the measurements.

    Transaction pooling re-assigns the *server* connection at every
    transaction boundary, so nothing prepared on one transaction is
    guaranteed to exist on the next. All three layers of prepared-statement
    reuse in this driver stack therefore have to go:

    - ``prepared_statement_cache_size=0`` — SQLAlchemy's own per-DBAPI-
      connection LRU of asyncpg PreparedStatement objects (default 100).
      Left on, the second execution of a cached statement re-uses a handle
      the new server connection has never heard of: asyncpg raises
      ``InvalidSQLStatementNameError: prepared statement "__asyncpg_stmt_1__"
      does not exist``. This is the one that actually fires in practice —
      reproduced in tests/test_pgbouncer.py against a real pgbouncer.
    - ``prepared_statement_name_func`` — asyncpg otherwise names statements
      from a per-connection counter (``__asyncpg_stmt_1__``, ``_2__``, …), so
      two *different* client connections multiplexed onto one server
      connection both claim the same name and collide with
      ``DuplicatePreparedStatementError``. UUID names make a collision
      impossible regardless of how pgbouncer shuffles connections.
    - ``statement_cache_size=0`` — asyncpg's own statement cache, below
      SQLAlchemy's. Belt-and-braces: the dialect always passes an explicit
      ``name=`` (which bypasses that cache today), but that's an
      implementation detail of the dialect, not a contract.

    The counterpart on the pgbouncer side is ``server_reset_query_always``,
    without which these uniquely-named statements accumulate on every server
    connection until it is recycled — see .env.pgbouncer.example.
    """
    if not settings.PGBOUNCER_ENABLED:
        return {}
    return {
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
        "prepared_statement_name_func": _unique_prepared_statement_name,
    }


def create_engine_for(url: str, settings: Settings) -> AsyncEngine:
    return create_async_engine(
        url,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_pre_ping=True,
        echo=_echo,
        # Kept even behind pgbouncer: SQLAlchemy's pool holds *client*
        # connections to the pooler, which is exactly what lets pgbouncer
        # multiplex them onto far fewer Postgres backends. NullPool
        # (SQLAlchemy's pgbouncer advice) is aimed at the prepared-statement
        # buildup that build_connect_args and server_reset_query_always
        # already prevent, and would trade that for a full TCP+auth handshake
        # on every checkout.
        connect_args=build_connect_args(settings),
    )


write_engine: AsyncEngine = create_engine_for(str(_settings.DATABASE_URL), _settings)

# T-246: the same object as write_engine unless READ_REPLICA_URL is set, which
# is what makes every `use_read_replica=True` read a no-op by default -- see
# should_route_to_replica() below and docs/adr/0003-read-replica.md.
read_engine: AsyncEngine = (
    write_engine
    if _settings.READ_REPLICA_URL is None
    else create_engine_for(str(_settings.READ_REPLICA_URL), _settings)
)

# Keeps its name (rather than becoming `write_session_factory`) because it is
# what every unrouted caller -- workers, seeds, get_session() itself -- has
# always used: this is the default, and the replica is the exception.
async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    write_engine, expire_on_commit=False, class_=AsyncSession
)
read_session_factory: async_sessionmaker[AsyncSession] = (
    async_session_factory
    if read_engine is write_engine
    else async_sessionmaker(read_engine, expire_on_commit=False, class_=AsyncSession)
)

# T-246, hard rule #1: reads that an access-control decision depends on never
# leave the primary. Replication lag on auth is not a stale-data annoyance, it
# is a security bug -- a revoked session, a consumed password-reset token or a
# deleted membership read from a replica that is 200ms behind is an
# authorization decision made against state that no longer exists.
#
# The definition-time guard in app.core.repository is the first line of defence
# (auth repositories expose no `use_read_replica` parameter at all); this set is
# the second, enforced per statement at execution time, so a join that reaches
# one of these tables from an otherwise-eligible read is pinned too. Kept in
# sync with the models by tests/test_read_replica.py, not by hand.
PRIMARY_ONLY_TABLES: frozenset[str] = frozenset(
    {
        # app.modules.auth.models
        "user",
        "session",
        "oauth_account",
        "totp_secret",
        "webauthn_credential",
        "email_verification_token",
        "password_reset_token",
        "magic_link_token",
        "email_change_token",
        # app.modules.tenancy.models -- membership *is* the tenant-isolation
        # check (§0.2), and an invitation is a credential that can be
        # consumed exactly once.
        "membership",
        "invitation",
    }
)


def read_replica_configured() -> bool:
    """True only when READ_REPLICA_URL gave us a genuinely separate engine."""
    return read_engine is not write_engine


def should_route_to_replica(statement: ClauseElement) -> bool:
    """Whether this statement may be answered by the replica.

    False whenever no replica is configured (the default deployment), and
    false for any statement that touches a PRIMARY_ONLY_TABLES table however
    indirectly -- the caller's opt-in is a *request*, not an override.
    """
    if not read_replica_configured():
        return False
    tables = {table.name for table in find_tables(statement, include_joins=True, include_crud=True)}
    pinned = tables & PRIMARY_ONLY_TABLES
    if pinned:
        # Not an exception: the correct behaviour is to answer the query from
        # the primary, which is what the caller gets. The log line exists
        # because reaching it means a read method asked for the replica on a
        # statement nobody expected to touch auth state -- worth knowing about.
        logger.warning("read_replica.pinned_to_primary: tables={}", sorted(pinned))
        return False
    return True


def _primary_unless_called_directly() -> bool:
    """Sub-dependency supplying get_session's `read_only` under FastAPI.

    `read_only: bool = False` alone would make FastAPI treat it as a *query
    parameter* -- `Depends(get_session)` appears on ~250 routes, so every one
    of them would grow a client-settable `?read_only=` in its OpenAPI schema,
    and routing would become something a caller could ask for over HTTP. That
    is precisely the "never implicit, never client-driven" rule this task
    exists to prevent, so the parameter is annotated as a dependency instead:
    FastAPI resolves it to False and never exposes it, while direct Python
    callers (read_session_scope below) pass it normally.
    """
    return False


def session_factory_for(*, read_only: bool) -> async_sessionmaker[AsyncSession]:
    """The one place the read/write factory choice is made.

    Both names are looked up on the module at call time, so a test that
    monkeypatches either (tests/test_db.py, tests/test_read_replica.py) is
    honoured by every caller.
    """
    return read_session_factory if read_only else async_session_factory


async def get_session(
    read_only: Annotated[bool, Depends(_primary_unless_called_directly)] = False,
) -> AsyncGenerator[AsyncSession, None]:
    session = session_factory_for(read_only=read_only)()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


@asynccontextmanager
async def read_session_scope() -> AsyncIterator[AsyncSession]:
    """A short-lived session on the replica, for a single read.

    Rows it returns are detached the moment it closes: they are a *snapshot*,
    not part of the caller's unit of work, and mutating one persists nothing.
    That is the whole point -- a write must go through the caller's own
    (primary) session. See app.core.repository's routing helpers, the only
    intended caller.
    """
    session = session_factory_for(read_only=True)()
    try:
        yield session
    finally:
        # close() releases the connection and rolls back the transaction it
        # implicitly opened; nothing here is ever committed.
        await session.close()


async def dispose_engine() -> None:
    await write_engine.dispose()
    if read_replica_configured():
        await read_engine.dispose()
