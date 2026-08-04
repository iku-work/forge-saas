from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

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


engine: AsyncEngine = create_async_engine(
    str(_settings.DATABASE_URL),
    pool_size=_settings.DB_POOL_SIZE,
    max_overflow=_settings.DB_MAX_OVERFLOW,
    pool_pre_ping=True,
    echo=_echo,
    # Kept even behind pgbouncer: SQLAlchemy's pool holds *client* connections
    # to the pooler, which is exactly what lets pgbouncer multiplex them onto
    # far fewer Postgres backends. NullPool (SQLAlchemy's pgbouncer advice) is
    # aimed at the prepared-statement buildup that build_connect_args and
    # server_reset_query_always already prevent, and would trade that for a
    # full TCP+auth handshake on every checkout.
    connect_args=build_connect_args(_settings),
)

async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine, expire_on_commit=False, class_=AsyncSession
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    session = async_session_factory()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def dispose_engine() -> None:
    await engine.dispose()
