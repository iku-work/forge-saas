# ADR-0003: Read-replica routing

## Status

Accepted — plumbing only. **No replica is provisioned**, and none should be
until the trigger below actually fires. `READ_REPLICA_URL` is unset in every
environment, which makes every routing decision in this document a no-op.

## Context

Reads dominate this application's database traffic, and they are not all
equally urgent. An admin paging through six months of audit events and a
`SELECT` that decides whether a session is still valid are the same kind of
statement to Postgres and completely different kinds of statement to us.

The scaling answer for the first kind is a read replica: a streaming-
replication standby that answers `SELECT`s, taking that load off the primary
so it can spend its CPU and its connection slots on writes and on the reads
that must be correct *right now*.

The problem with adopting one is never the infrastructure — a managed replica
is a checkbox on Render and one more service in `docker-compose.prod.yml`. The
problem is that "which queries may go there" is a decision spread across every
repository method in the codebase, and answering it retroactively, under load,
during an incident, is how a replica turns into an outage. A standby is
asynchronous: it applies the primary's WAL some milliseconds — occasionally
some seconds — later. Every query sent to it is answered from a version of the
database that is *definitely* in the past by an amount nobody controls.

So the decision that needs recording is not "should we use a replica" but
"what shape does read routing have when we do" — and that shape can exist,
and be tested, before the replica does.

## Decision

### 1. Two engines, one of them usually the same object

`app.core.db` exposes `write_engine` and `read_engine`. When `READ_REPLICA_URL`
is unset — the default, and the state of every environment today —
`read_engine` **is** `write_engine` (the same Python object), and
`read_session_factory` **is** `async_session_factory`. There is no second pool,
no second connection, no branch to get wrong: a deployment without a replica
behaves exactly as it did before this ADR, and `read_replica_configured()`
returns False.

`READ_REPLICA_URL` is validated like any other DSN — outside dev it must carry
`sslmode=require` or stricter, since it is a third independent hop carrying
exactly the same rows as the primary.

### 2. Routing is per repository method, opt-in, and never implicit

A read that tolerates lag takes `use_read_replica: bool = False` and runs its
statement through `ReplicaReadMixin._fetch_all` / `_fetch_one`
(`app.core.repository`). Today that is `BaseRepository.get`,
`BaseRepository.list_cursor`, and `AuditRepository.list_cursor`.

Three things this deliberately is **not**:

- **Not by HTTP verb.** "GET goes to the replica" is the design that looks
  obvious in a blog post and fails on the first `GET /billing/portal` that
  reads a subscription it just created. The method knows what its data is
  worth; the router does not.
- **Not client-settable.** `get_session(read_only=...)`'s parameter is
  annotated as a FastAPI dependency rather than left as a bare `bool = False`,
  because a bare default would make FastAPI publish `?read_only=` as a query
  parameter on all ~250 routes that depend on it. Routing must never be
  something a caller can ask for over HTTP. A test asserts the OpenAPI schema
  stays clean.
- **Not a default anywhere.** Every call site in the codebase leaves the flag
  at False. Turning one on is a deliberate, reviewable change — see the
  activation checklist.

### 3. Two hard rules

**Auth reads always hit the primary.** Sessions, tokens, credentials,
memberships, invitations. Replication lag on an authorization decision is not
a stale-data annoyance, it is a security bug: a revoked session, a consumed
password-reset token or a deleted membership read from a standby that is 200ms
behind is an access-control decision made against state that no longer exists.
This is enforced twice —

- the auth repositories expose no `use_read_replica` parameter at all (there is
  nothing to ask for), and a test asserts they never grow one;
- `app.core.db.PRIMARY_ONLY_TABLES` pins the tables themselves, checked per
  *statement* at execution time, so an otherwise-eligible read that joins or
  sub-selects its way into auth state is pinned too. A pinned statement is
  answered by the primary and logged (`read_replica.pinned_to_primary`); it is
  not an error, because the correct data is what the caller gets.

**Read-your-writes stays on the primary.** A flow that creates something and
then lists it within the same request must not read the list from a standby
that has not seen the insert. In practice this falls out of the design rather
than needing policing: the flag is off by default, and a service that just
wrote through its session is holding the primary session anyway. Rows returned
from the replica are also *detached snapshots* — the replica session closes
immediately, so a row read from it can never be mutated-and-flushed by
accident. The write path is the caller's own session, always.

### 4. The misuse guard

`use_read_replica` on a method that writes would be a silent data-loss bug:
the write would execute against a standby (rejected outright by a real one,
or — worse, in a test with a writable stand-in — applied somewhere nothing
ever reads). So it is rejected at *class-definition* time, not at call time.

`check_replica_flags` (`app.core.repository`) runs from
`ReplicaReadMixin.__init_subclass__` and requires that any method carrying the
flag be named like a read: first token in `get/list/count/exists/find/search/
fetch/total/has/any`, and no write verb anywhere in the name. `create` is
rejected; so is `get_or_create`; so is an ambiguous `sync_everything`. The
hand-rolled repositories that don't inherit the mixin are swept by the same
function in `tests/test_read_replica.py`, so the flag cannot reach a mutating
method anywhere in the codebase.

This is a name heuristic, and it is honest about being one: it catches the
mistake somebody would actually make, and it makes renaming the method the
cheapest way out.

### 5. Which query classes are lag-tolerant

| Query class | Verdict | Why |
|---|---|---|
| Audit event feeds (`AuditRepository.list_cursor`) | **eligible** | Append-only history, read by humans looking at the past. Nobody writes an audit event and re-reads it in the same breath. |
| Admin directory listings, usage/analytics rollups, reporting | **eligible** | Aggregates over data that is already minutes stale by nature. |
| Org-scoped resource lists (`BaseRepository.list_cursor`) | **eligible with care** | Fine for browsing; *not* fine on the page a user lands on straight after creating the resource. Per call site, not per method. |
| Detail reads (`BaseRepository.get`) | **eligible with care** | Same rule, plus: never for a row that is about to be updated — use `get_or_raise`, which has no flag. |
| Sessions, tokens, credentials, 2FA, memberships, invitations | **never** | Rule 3. Enforced, not advised. |
| Billing state feeding a charge or entitlement decision | **never** | A stale subscription row is money. |
| Anything read inside a transaction that also writes | **never** | Two connections, two snapshots, no atomicity between them. |
| Counts backing a plan-limit check | **never** | The check exists to stop the write that follows it. |

## When to actually enable this

Two triggers, either one sufficient. Both are read-load triggers — a primary
that is busy *writing* is not helped by a replica.

1. **Sustained primary CPU above 60%, attributable to read traffic.** Check
   the host CPU on the provider's own dashboard (Render metrics, or the
   docker host for a self-hosted stack), then confirm the reads are the cause
   with the "hall of shame — top 10 statements by total execution time" panel
   on `docs/grafana/dashboards/database.json` (Forge · Database, uid
   `forge-database`, T-205). If the top entries by total execution time are
   `SELECT`s from the eligible classes above, a replica moves real work. If
   they are writes, or one pathological query, fix that instead —
   `docs/runbooks/slow-queries.md`.
2. **Pool exhaustion caused by reads.** The "Pool utilization" panel on the
   same dashboard (`100 * forge_db_pool_used / forge_db_pool_size`) pinned
   near 100% while request latency climbs, with `http_requests_inprogress` on
   `api-overview.json` (T-204) showing requests queuing rather than a traffic
   spike. Note the cheaper fixes first: raise `DB_POOL_SIZE` /
   `DB_MAX_OVERFLOW` (T-223 sized the defaults for a single un-scaled
   replica), and check that pgbouncer is actually in front of Postgres
   (ADR-0002). A replica is the answer when those are already done and the
   read volume itself is the problem.

Explicitly *not* triggers: an upcoming launch, a slow single query, general
anxiety about scale. A replica doubles the number of databases that can be
down, lagging, failed-over, or out of disk.

## Activation checklist

1. Provision the standby (managed: Render/RDS read replica; self-hosted: a
   streaming-replication standby with a monitored `pg_stat_replication`).
2. Set `READ_REPLICA_URL` on the backend **and** the worker, with
   `sslmode=require`. Never on Alembic: migrations use
   `MIGRATIONS_DATABASE_URL`, which bypasses both the pooler and the replica.
3. Confirm the replica is actually read-only (`SHOW transaction_read_only` →
   `on`). A writable stand-in silently accepts writes that then vanish.
4. Turn the flag on at **one** call site — `AuditRepository.list_cursor` from
   the admin audit feed is the intended first adopter — and ship that alone.
5. Add replica observability before the second call site, not after:
   - a `forge_db_replica_pool_used`/`_size` gauge pair (mirror
     `_read_db_pool_used` in `app.core.metrics`, which reads `write_engine`
     only), and a panel for it on `database.json`;
   - replication lag as a panel *and* an alert in `docs/grafana/alerts.yml`.
     Nothing scrapes it today: postgres-exporter's built-in replication
     collector is not enabled, and `docs/grafana/postgres-exporter-queries.yaml`
     has no query for it — add one over `pg_last_xact_replay_timestamp()` on
     the standby. Lag is the failure mode this whole design is built around,
     and it is currently unmeasured;
   - extend `check_database_ready` (`app.main`) to probe the replica, reporting
     degraded rather than not-ready when only the replica is unreachable.
6. Verify tracing shows replica spans: `configure_tracing` instruments
   `read_engine.sync_engine` separately once it is a distinct engine.
7. Write the failure drill into `docs/runbooks/`: what to do when lag spikes
   (unset `READ_REPLICA_URL` and redeploy — the code path collapses back to
   primary-only with no code change, which is the point of the default-off
   design) and when the standby is promoted.

## Consequences

- The routing code path exists, is typed, and is tested against a real second
  Postgres today. Enabling a replica later is an infrastructure change plus a
  one-line flag per call site — not a refactor of every repository.
- The default deployment pays nothing: one engine, one pool, identical
  behaviour, and the full suite green with the flag unset.
- A `use_read_replica=True` read returns detached rows. That is a deliberate
  constraint (it makes accidental writes-through-a-replica-row impossible) but
  it means a caller cannot mutate what it read — it must re-load through the
  primary session. Any relationship not eagerly loaded is unavailable on those
  rows.
- Two enforcement layers for the auth rule (no parameter + per-statement pin)
  is duplication on purpose: the first is the API surface, the second survives
  someone adding a flag to a repository the first layer never anticipated.
- `PRIMARY_ONLY_TABLES` is a list of table names that must track the models.
  It is kept honest by a drift test rather than by discipline — a new auth
  model that nobody adds to the set fails the build.
- The name-based misuse guard has a false-negative class (a mutating method
  named entirely in read verbs) and a false-positive class (a genuine read
  named `get_or_refresh_something`). Both resolve by renaming the method,
  which is the cheaper fix in either direction.
