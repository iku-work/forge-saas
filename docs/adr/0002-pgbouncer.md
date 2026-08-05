# ADR-0002: PgBouncer in transaction mode

## Status

Accepted

## Context

A Postgres backend costs roughly 5–10 MB of server memory and a scheduler
slot, and `max_connections` is a hard ceiling set at postmaster start. The
application holds `DB_POOL_SIZE + DB_MAX_OVERFLOW` connections *per replica*
(defaults: 5 + 15 = 20). Scaling the backend horizontally therefore multiplies
Postgres connections by the replica count, and the ceiling arrives long before
any interesting CPU or IO limit does: at the default `max_connections=100`,
five replicas exhaust the database.

A connection pooler in front of Postgres decouples the two numbers — the app
opens as many *client* connections as it likes, and the pooler multiplexes
them onto a much smaller, fixed set of *server* connections.

The complication is that this application talks to Postgres over asyncpg, and
asyncpg leans on server-side prepared statements for every statement it
executes. Prepared statements are per-connection state, and the whole point of
a pooler in transaction mode is that the connection underneath you changes.
That interaction is the substance of this decision — the pooler itself is
otherwise unremarkable.

## Decision

### 1. PgBouncer, in transaction mode

`docker-compose.prod.yml` gains a pinned `edoburu/pgbouncer:v1.25.2-p0`
service. All backend and worker traffic goes through it (`DATABASE_URL` points
at `pgbouncer:6432`); `depends_on: pgbouncer: service_healthy` gates boot on a
healthcheck that opens a real pooled connection to the real database, not just
a TCP probe.

`pool_mode = transaction` is the only mode that achieves the goal:

| mode | server connection is held for | verdict |
|---|---|---|
| session | the client connection's whole lifetime | pointless here — the connection count stays 1:1, which is exactly what we're trying to break |
| transaction | one transaction | chosen |
| statement | one statement | would break every multi-statement transaction in the app |

Sizing, against Postgres' default `max_connections = 100` and
`superuser_reserved_connections = 3`: `default_pool_size = 75` plus
`reserve_pool_size = 5` caps server connections at 80 (80% of
`max_connections`), leaving the 3 reserved superuser slots plus headroom for
the `postgres-exporter` service, Alembic, and ad-hoc `psql` — all of which
connect directly, bypassing the pooler. `max_client_conn = 1000` is the client
side and is cheap: an idle client connection costs pgbouncer a socket, not a
Postgres backend. The full annotated configuration is
`.env.pgbouncer.example`.

**Dev stays direct.** `docker-compose.yml` connects to Postgres with no pooler
in between — one less moving part locally, and `PGBOUNCER_ENABLED` defaults to
`false`, so nothing below applies to a developer machine.

### 2. Migrations bypass the pooler

`MIGRATIONS_DATABASE_URL` (new setting) is what `alembic/env.py` connects
with; it points straight at `postgres:5432`. Transaction pooling and schema
migrations are incompatible for two independent reasons:

- Alembic guards the version table with a **session-level advisory lock**.
  Session-level locks are released when the session ends — under transaction
  pooling the "session" is whatever server connection this transaction
  happened to get, so the lock protects nothing and can be silently dropped
  between statements.
- A single migration script issues **multiple DDL statements**, which
  transaction pooling may spread across different backends. Postgres DDL is
  transactional, but only within one connection.

The setting defaults to `None`, meaning "same as `DATABASE_URL`" — dev,
`.env.test`, and the Render deployment (no pooler in the path) are unaffected.
Once `PGBOUNCER_ENABLED=true`, `Settings` refuses to start unless
`MIGRATIONS_DATABASE_URL` is set *and differs from* `DATABASE_URL`: a
deployment that turns the pooler on and forgets this fails at boot rather than
halfway through a production migration.

Outside dev the migration DSN carries the same `sslmode=require` requirement
as `DATABASE_URL` (ASVS V9.2.2) — it is a second, independent hop to Postgres,
and `DATABASE_URL`'s own TLS setting says nothing about it.

### 3. The prepared-statement tradeoff

`PGBOUNCER_ENABLED=true` makes `app/core/db.py` build the engine with three
driver arguments (`build_connect_args`), which together disable every layer of
prepared-statement reuse in the stack:

| argument | what it turns off | what breaks without it |
|---|---|---|
| `prepared_statement_cache_size=0` | SQLAlchemy's per-DBAPI-connection LRU of asyncpg `PreparedStatement` objects (default 100) | the second execution of a cached statement re-uses a handle the newly-assigned server connection has never seen: `InvalidSQLStatementNameError: prepared statement "__asyncpg_stmt_1__" does not exist` |
| `prepared_statement_name_func` (UUID names) | asyncpg's per-connection counter naming (`__asyncpg_stmt_1__`, `_2__`, …) | two different client connections multiplexed onto one server connection both claim the same name: `DuplicatePreparedStatementError` |
| `statement_cache_size=0` | asyncpg's own statement cache, below SQLAlchemy's | nothing today — the dialect always passes an explicit `name=`, which bypasses that cache — but that is an implementation detail of the dialect, not a contract it promises |

`InvalidSQLStatementNameError` is the failure that actually fires: it is
reproduced against a real pgbouncer container in
`backend/tests/test_pgbouncer.py`, which asserts both that the unconfigured
engine fails and that the configured one doesn't.

The counterpart on the pgbouncer side is `server_reset_query_always = 1` with
`server_reset_query = DISCARD ALL`. Because names are now unique and nothing
is cached, no prepared statement is ever reused — so without a reset on
release they accumulate on every server connection until it is recycled
(`server_lifetime`, one hour by default). Measured on a synthetic churn
workload (20 client connections × 25 iterations over 4 server connections):
**~115 prepared statements per server connection and still climbing** without
the reset query, **1** with it. In transaction mode pgbouncer only runs
`server_reset_query` when `server_reset_query_always` is on, so both settings
are required.

SQLAlchemy's own pgbouncer guidance additionally suggests `NullPool`. We keep
the normal `QueuePool`: its purpose there is to bound exactly the
prepared-statement buildup that the reset query already handles, and
`NullPool` would pay a full TCP + SCRAM handshake on every checkout while
throwing away the client-side pooling that lets pgbouncer multiplex in the
first place.

### Measured overhead

Two separate costs, measured separately (Docker Desktop / Windows loopback,
Postgres 16, pgbouncer 1.25.2, asyncpg 0.31, SQLAlchemy 2.0.51 — the absolute
numbers are inflated by a ~1 ms loopback RTT on this rig, so read the deltas,
not the totals):

| measurement | median | p95 |
|---|---|---|
| `SELECT 1` direct to Postgres | 0.96 ms | 1.48 ms |
| `SELECT 1` through pgbouncer | 1.86 ms | 2.89 ms |
| **proxy hop** | **+0.89 ms** | **+1.41 ms** |
| join query, prepared-statement cache on, direct | 1.17–1.23 ms | 1.81–1.84 ms |
| join query, cache off (this ADR's settings), direct | 1.85–1.88 ms | 2.63–2.67 ms |
| **cache disabled** | **+0.65 ms** | **+0.83 ms** |

Both are one extra round trip: one for the proxy hop, one for the re-PARSE
that a disabled statement cache forces on every execution. Each is a fixed
per-statement cost — it does not scale with how expensive the query itself is,
so it is proportionally largest on exactly the trivial queries measured above
and negligible on the reporting queries that dominate real latency. On a
datacenter link (sub-0.3 ms RTT, rather than this rig's ~1 ms) both deltas
shrink accordingly.

That is the price of the connection-count ceiling disappearing. Measured on
the same rig: **20 application connections resolved to 4 Postgres backends**,
and that number is bounded by `default_pool_size`, not by replica count.

### Revisiting this

The re-parse cost is the part worth revisiting if it ever shows up in the
p99 (watch `pg_stat_statements` / the T-205 dashboards). Two escape hatches,
in order of preference:

1. **pgbouncer's own prepared-statement support.** pgbouncer 1.21+ tracks
   prepared statements per client and re-prepares them on whichever server
   connection it assigns (`max_prepared_statements`, currently pinned at `0`).
   Enabling it requires reverting the app-side settings in the same change —
   with UUID statement names, pgbouncer's cache would fill with names nothing
   ever asks for again. This is the modern answer and the one to reach for
   first.
2. **A read replica for the lag-tolerant read traffic** (T-246), which reduces
   the statement volume hitting this path at all rather than making each
   statement cheaper. The routing for it exists and is tested
   (`docs/adr/0003-read-replica.md`); no replica is provisioned, and that ADR
   holds the trigger for changing that.

## Consequences

- Postgres backend count is now a function of `default_pool_size`, not replica
  count. Scaling the backend no longer risks `FATAL: sorry, too many clients
  already`.
- Two DSNs to keep in sync in production (`DATABASE_URL` at the pooler,
  `MIGRATIONS_DATABASE_URL` at Postgres). Startup validation makes getting
  this wrong loud instead of subtle.
- One more service in the prod stack, and one more thing that can be down —
  mitigated by the end-to-end healthcheck plus `depends_on:
  service_healthy`, so the backend never starts against a pooler that can't
  reach Postgres.
- Session-scoped Postgres features are off the table on the app path:
  `LISTEN`/`NOTIFY`, session-level advisory locks, `SET` outside a
  transaction, and cursors held across transactions. None are used today; a
  future feature needing one must use `MIGRATIONS_DATABASE_URL`-style direct
  access, not the pooled path.
- `docker-compose.prod.yml`'s postgres service gained `.env.postgres` and both
  Python services' build context moved to the repo root. Neither is really
  about pgbouncer — both were pre-existing defects (no `POSTGRES_PASSWORD`,
  and a build context that couldn't satisfy `backend/Dockerfile`'s `COPY`s)
  that surfaced the moment CI started actually booting this stack.
