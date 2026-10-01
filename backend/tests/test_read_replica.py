"""T-246: the read-replica code path, exercised against a real second Postgres.

The plumbing is default-off -- with READ_REPLICA_URL unset, `read_engine` *is*
`write_engine` and every `use_read_replica=True` read is answered by the
caller's own session. That default is asserted here first, because it is what
lets the rest of the suite (and every existing deployment) stay unchanged.

Everything after that runs against two independent PostgresContainers standing
in for a primary and its replica. They are not actually replicating -- that is
the point: rows are seeded into one and not the other, so "which database
answered this query" is decidable from the result alone. Routing is also
checked at the connection level, by recording every statement each engine
executes.

See docs/adr/0003-read-replica.md.
"""

import importlib
import inspect
import pkgutil
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import Field, SQLModel
from testcontainers.postgres import PostgresContainer

import app.modules
from app.core import db
from app.core.repository import REPLICA_FLAG, BaseRepository, ReplicaReadMixin, check_replica_flags
from app.main import app as fastapi_app
from app.models._mixins import SoftDeleteMixin, TimestampMixin, UUIDPKMixin
from app.modules.audit.models import AuditEvent
from app.modules.auth.models import (
    EmailChangeToken,
    EmailVerificationToken,
    MagicLinkToken,
    OAuthAccount,
    PasswordResetToken,
    TOTPSecret,
    User,
    WebAuthnCredential,
)
from app.modules.auth.models import Session as SessionModel
from app.modules.tenancy.models import Invitation, Membership, Organization


class ReplicaEntity(UUIDPKMixin, TimestampMixin, SoftDeleteMixin, SQLModel, table=True):
    __tablename__ = "replica_entity"
    __test__: ClassVar[bool] = False

    org_id: UUID = Field(index=True)
    name: str


class ReplicaEntityRepository(BaseRepository[ReplicaEntity]):
    __test__: ClassVar[bool] = False
    model = ReplicaEntity


# --------------------------------------------------------------------------
# Default deployment: no replica configured
# --------------------------------------------------------------------------


def test_both_engine_names_bind_to_one_engine_by_default() -> None:
    # .env.test sets no READ_REPLICA_URL, which is the whole point: this is
    # the state every existing test in the suite runs in.
    assert db.read_engine is db.write_engine
    assert db.read_session_factory is db.async_session_factory
    assert db.read_replica_configured() is False


def test_flagged_reads_resolve_to_the_primary_factory_by_default() -> None:
    assert db.session_factory_for(read_only=True) is db.async_session_factory
    assert db.session_factory_for(read_only=False) is db.async_session_factory


def test_nothing_is_eligible_for_the_replica_by_default() -> None:
    assert db.should_route_to_replica(select(AuditEvent)) is False


def test_get_session_adds_no_query_parameter_to_any_route() -> None:
    # get_session's `read_only` is annotated as a dependency rather than left
    # as a bare `bool = False` precisely so FastAPI never sees it as a query
    # parameter. Left bare, all ~250 routes that Depends(get_session) would
    # grow a client-settable `?read_only=` -- i.e. read routing would become
    # something an HTTP caller could ask for, which is the one thing this
    # design forbids.
    schema = fastapi_app.openapi()
    offenders = [
        f"{method.upper()} {path}"
        for path, operations in schema["paths"].items()
        for method, operation in operations.items()
        if isinstance(operation, dict)
        for parameter in operation.get("parameters", [])
        if parameter["name"] == REPLICA_FLAG or parameter["name"] == "read_only"
    ]
    assert offenders == []


# --------------------------------------------------------------------------
# Live primary + fake replica
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def primary_container() -> Generator[PostgresContainer, None, None]:
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture(scope="module")
def replica_container() -> Generator[PostgresContainer, None, None]:
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


async def _engine_for(container: PostgresContainer) -> AsyncEngine:
    url = container.get_connection_url().replace("psycopg2", "asyncpg")
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(
            SQLModel.metadata.create_all, tables=[cast("Any", ReplicaEntity).__table__]
        )
    return engine


# Function-scoped: pytest-asyncio gives each test its own event loop, and an
# asyncpg-backed engine can't be reused across loops (see test_base_repository).
@pytest.fixture
async def primary_engine(primary_container: PostgresContainer) -> AsyncGenerator[AsyncEngine, None]:
    engine = await _engine_for(primary_container)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(
            SQLModel.metadata.drop_all, tables=[cast("Any", ReplicaEntity).__table__]
        )
    await engine.dispose()


@pytest.fixture
async def replica_engine(replica_container: PostgresContainer) -> AsyncGenerator[AsyncEngine, None]:
    engine = await _engine_for(replica_container)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(
            SQLModel.metadata.drop_all, tables=[cast("Any", ReplicaEntity).__table__]
        )
    await engine.dispose()


class StatementLog:
    """Every SQL statement each engine actually executed, in order."""

    def __init__(self) -> None:
        self.primary: list[str] = []
        self.replica: list[str] = []

    def selects(self, statements: list[str]) -> list[str]:
        return [s for s in statements if s.lstrip().upper().startswith("SELECT")]


@pytest.fixture
def statements(primary_engine: AsyncEngine, replica_engine: AsyncEngine) -> StatementLog:
    log = StatementLog()

    def _listener(sink: list[str]) -> Any:  # noqa: ANN401 -- SQLAlchemy listener signature
        def _record(*args: Any) -> None:  # noqa: ANN401
            # before_cursor_execute(conn, cursor, statement, parameters,
            # context, executemany) -- only the statement is interesting here.
            sink.append(args[2])

        return _record

    event.listen(primary_engine.sync_engine, "before_cursor_execute", _listener(log.primary))
    event.listen(replica_engine.sync_engine, "before_cursor_execute", _listener(log.replica))
    return log


@pytest.fixture
async def replica_configured(
    monkeypatch: pytest.MonkeyPatch, primary_engine: AsyncEngine, replica_engine: AsyncEngine
) -> None:
    """What app.core.db looks like once READ_REPLICA_URL is set."""
    monkeypatch.setattr(db, "write_engine", primary_engine)
    monkeypatch.setattr(db, "read_engine", replica_engine)
    monkeypatch.setattr(
        db,
        "async_session_factory",
        async_sessionmaker(primary_engine, expire_on_commit=False, class_=AsyncSession),
    )
    monkeypatch.setattr(
        db,
        "read_session_factory",
        async_sessionmaker(replica_engine, expire_on_commit=False, class_=AsyncSession),
    )
    assert db.read_replica_configured() is True


@pytest.fixture
def org_id() -> UUID:
    return uuid4()


async def _seed(engine: AsyncEngine, *, org_id: UUID, name: str, minute: int) -> UUID:
    entity = ReplicaEntity(
        org_id=org_id,
        name=name,
        created_at=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(minutes=minute),
    )
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        session.add(entity)
        await session.commit()
    return entity.id


@pytest.fixture
async def repo(
    replica_configured: None,  # noqa: ARG001 -- ordering only
    primary_engine: AsyncEngine,
    org_id: UUID,
) -> AsyncGenerator[ReplicaEntityRepository, None]:
    factory = async_sessionmaker(primary_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield ReplicaEntityRepository(session, org_id)


async def test_flagged_get_is_answered_by_the_replica(
    repo: ReplicaEntityRepository,
    replica_engine: AsyncEngine,
    org_id: UUID,
    statements: StatementLog,
) -> None:
    replica_only = await _seed(replica_engine, org_id=org_id, name="on-replica", minute=0)
    statements.primary.clear()
    statements.replica.clear()

    fetched = await repo.get(replica_only, use_read_replica=True)

    assert fetched is not None
    assert fetched.name == "on-replica"
    assert statements.selects(statements.replica) != []
    assert statements.selects(statements.primary) == []


async def test_the_same_get_without_the_flag_never_leaves_the_primary(
    repo: ReplicaEntityRepository,
    replica_engine: AsyncEngine,
    org_id: UUID,
    statements: StatementLog,
) -> None:
    replica_only = await _seed(replica_engine, org_id=org_id, name="on-replica", minute=0)
    statements.primary.clear()
    statements.replica.clear()

    assert await repo.get(replica_only) is None
    assert statements.selects(statements.primary) != []
    assert statements.selects(statements.replica) == []


async def test_flagged_list_cursor_pages_the_replica(
    repo: ReplicaEntityRepository,
    primary_engine: AsyncEngine,
    replica_engine: AsyncEngine,
    org_id: UUID,
) -> None:
    await _seed(primary_engine, org_id=org_id, name="on-primary", minute=0)
    for i in range(3):
        await _seed(replica_engine, org_id=org_id, name=f"replica-{i}", minute=i)

    page, cursor = await repo.list_cursor(limit=2, use_read_replica=True)
    assert [e.name for e in page] == ["replica-2", "replica-1"]

    # The cursor's anchor row also has to be resolved on the replica -- looked
    # up on the primary it would not exist, and the second page would silently
    # restart from the top.
    page_two, _ = await repo.list_cursor(after=cursor, limit=2, use_read_replica=True)
    assert [e.name for e in page_two] == ["replica-0"]

    unrouted, _ = await repo.list_cursor(limit=2)
    assert [e.name for e in unrouted] == ["on-primary"]


async def test_replica_rows_are_detached_snapshots(
    repo: ReplicaEntityRepository, replica_engine: AsyncEngine, org_id: UUID
) -> None:
    entity_id = await _seed(replica_engine, org_id=org_id, name="snapshot", minute=0)

    fetched = await repo.get(entity_id, use_read_replica=True)

    assert fetched is not None
    # Loaded column values survive the replica session closing...
    assert fetched.name == "snapshot"
    # ...but the row is not part of the caller's unit of work, so a mutation
    # of it can never be flushed to the primary by accident.
    assert fetched not in repo.session.sync_session


async def test_a_pinned_table_stays_on_the_primary_even_when_flagged(  # noqa: PLR0913 -- one fixture per moving part
    repo: ReplicaEntityRepository,
    primary_engine: AsyncEngine,
    replica_engine: AsyncEngine,
    org_id: UUID,
    statements: StatementLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Stand-in for an auth table: the real ones are unreachable from a
    # BaseRepository (they are not org-scoped and expose no flag at all), so
    # the pin is exercised by pinning this test's own table instead.
    monkeypatch.setattr(db, "PRIMARY_ONLY_TABLES", frozenset({str(ReplicaEntity.__tablename__)}))
    on_primary = await _seed(primary_engine, org_id=org_id, name="on-primary", minute=0)
    await _seed(replica_engine, org_id=org_id, name="on-replica", minute=0)
    statements.primary.clear()
    statements.replica.clear()

    fetched = await repo.get(on_primary, use_read_replica=True)

    assert fetched is not None
    assert fetched.name == "on-primary"
    assert statements.selects(statements.replica) == []


# --------------------------------------------------------------------------
# Hard rule: auth reads never leave the primary
# --------------------------------------------------------------------------

_AUTH_MODELS = [
    User,
    SessionModel,
    OAuthAccount,
    TOTPSecret,
    WebAuthnCredential,
    EmailVerificationToken,
    PasswordResetToken,
    MagicLinkToken,
    EmailChangeToken,
]


@pytest.mark.parametrize("model", [*_AUTH_MODELS, Membership, Invitation])
async def test_auth_statements_are_never_eligible_for_the_replica(
    replica_configured: None,  # noqa: ARG001 -- ordering only
    model: type[SQLModel],
) -> None:
    assert db.should_route_to_replica(select(model)) is False


async def test_a_join_that_reaches_a_pinned_table_is_pinned_too(
    replica_configured: None,  # noqa: ARG001 -- ordering only
) -> None:
    # The pin is per statement, not per model: an otherwise-eligible read that
    # joins (or sub-selects) its way to auth state is pinned as well.
    org = cast("Any", Organization)
    membership = cast("Any", Membership)
    joined = select(Organization).join(Membership, membership.org_id == org.id)
    subquery = select(Organization).where(org.id.in_(select(membership.org_id)))

    assert db.should_route_to_replica(joined) is False
    assert db.should_route_to_replica(subquery) is False
    assert db.should_route_to_replica(select(AuditEvent)) is True


def test_primary_only_tables_covers_every_auth_model() -> None:
    # Drift guard: a new auth model must be added to PRIMARY_ONLY_TABLES, not
    # discovered to be missing from it during an incident.
    auth_tables = {str(cast("Any", model).__tablename__) for model in _AUTH_MODELS}
    assert auth_tables <= db.PRIMARY_ONLY_TABLES
    assert {str(cast("Any", Membership).__tablename__)} <= db.PRIMARY_ONLY_TABLES
    assert {str(cast("Any", Invitation).__tablename__)} <= db.PRIMARY_ONLY_TABLES


# --------------------------------------------------------------------------
# Misuse guard
# --------------------------------------------------------------------------


def test_flag_on_a_mutating_method_raises_at_definition_time() -> None:
    with pytest.raises(TypeError, match="is not a read"):

        class WritesToTheReplica(ReplicaReadMixin):
            async def create(self, *, use_read_replica: bool = False) -> None: ...


def test_flag_on_a_read_shaped_name_that_hides_a_write_raises() -> None:
    with pytest.raises(TypeError, match="is not a read"):

        class SneakyWriter(ReplicaReadMixin):
            async def get_or_create(self, *, use_read_replica: bool = False) -> None: ...


def test_flag_on_an_ambiguous_name_raises() -> None:
    with pytest.raises(TypeError, match="is not a read"):

        class Ambiguous(ReplicaReadMixin):
            async def sync_everything(self, *, use_read_replica: bool = False) -> None: ...


def test_flag_on_a_read_method_is_accepted() -> None:
    class Reader(ReplicaReadMixin):
        async def list_recent(self, *, use_read_replica: bool = False) -> None: ...

        async def count_all(self, *, use_read_replica: bool = False) -> None: ...

    assert Reader is not None


def test_an_unflagged_mutating_method_is_left_alone() -> None:
    class Writer(ReplicaReadMixin):
        async def create(self) -> None: ...

    assert Writer is not None


def _repository_classes() -> list[type]:
    classes: list[type] = []
    for module_info in pkgutil.iter_modules(app.modules.__path__):
        try:
            module = importlib.import_module(f"app.modules.{module_info.name}.repository")
        except ModuleNotFoundError:
            continue
        classes.extend(
            member
            for _, member in inspect.getmembers(module, inspect.isclass)
            if member.__module__ == module.__name__
        )
    return classes


def test_every_repository_in_the_codebase_passes_the_guard() -> None:
    # The mixin's __init_subclass__ only fires for classes that inherit it;
    # the hand-rolled repositories (auth, tenancy, billing, ...) are swept here
    # so the flag cannot reach a mutating method anywhere.
    repositories = _repository_classes()
    assert repositories != []  # the sweep found something to sweep
    for repository in repositories:
        check_replica_flags(repository)


def test_no_auth_repository_exposes_the_replica_flag() -> None:
    # Hard rule, enforced at the API surface rather than at execution time:
    # there is no way to *ask* for a replica read of auth state.
    auth_repositories = importlib.import_module("app.modules.auth.repository")
    for _, repository in inspect.getmembers(auth_repositories, inspect.isclass):
        if repository.__module__ != auth_repositories.__name__:
            continue
        for name, member in inspect.getmembers(repository, callable):
            if name.startswith("_"):
                continue
            assert REPLICA_FLAG not in inspect.signature(member).parameters, (
                f"{repository.__name__}.{name}() must not offer {REPLICA_FLAG}"
            )
