"""
Counts the SQL statements a block of code executes (T-243's fixture, first
needed by T-240's "the second get_current_user hits zero queries" proof).

Hooks SQLAlchemy's `before_cursor_execute` on the *connection the test
session is bound to*, not on the engine: conftest binds every test's session
to one Connection inside an outer transaction, so listening there counts
exactly the statements the code under test issued and nothing from a
neighbouring test's setup.

On failure it prints every captured statement rather than just the count --
an N+1 is obvious the moment you can see the same SELECT twenty times with a
different bind parameter, and unreadable as the number "23".
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

# Statements SQLAlchemy issues to manage the test harness's own transaction
# scaffolding (conftest's savepoint-per-commit trick), not application reads.
_IGNORED_PREFIXES = ("SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT", "BEGIN", "COMMIT")


@dataclass
class QueryLog:
    statements: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.statements)

    def render(self) -> str:
        if not self.statements:
            return "(no statements)"
        return "\n".join(f"  {i + 1:>3}. {sql}" for i, sql in enumerate(self.statements))


def _normalize(statement: str) -> str:
    return " ".join(statement.split())


@asynccontextmanager
async def count_queries(session: AsyncSession) -> AsyncIterator[QueryLog]:
    """Records every statement executed on `session`'s connection."""
    log = QueryLog()
    sync_target = (await session.connection()).sync_connection
    assert sync_target is not None  # always set once the AsyncConnection exists

    def _on_execute(
        _conn: Any,  # noqa: ANN401 -- SQLAlchemy listener signature
        _cursor: Any,  # noqa: ANN401
        statement: str,
        _parameters: Any,  # noqa: ANN401
        _context: Any,  # noqa: ANN401
        _executemany: bool,
    ) -> None:
        normalized = _normalize(statement)
        if normalized.upper().startswith(_IGNORED_PREFIXES):
            return
        log.statements.append(normalized)

    event.listen(sync_target, "before_cursor_execute", _on_execute)
    try:
        yield log
    finally:
        event.remove(sync_target, "before_cursor_execute", _on_execute)


@asynccontextmanager
async def assert_max_queries(session: AsyncSession, n: int) -> AsyncIterator[QueryLog]:
    """Fails if the block issues more than `n` statements, printing all of
    them so the offending N+1 is visible in the diff, not just a number."""
    async with count_queries(session) as log:
        yield log
    assert log.count <= n, (
        f"expected at most {n} SQL statement(s), got {log.count}:\n{log.render()}"
    )
