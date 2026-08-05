# Slow-query runbook (T-205)

Postgres's `pg_stat_statements` extension tracks per-normalized-statement
timing across the whole server. `prometheuscommunity/postgres-exporter`
scrapes it (`docs/grafana/postgres-exporter-queries.yaml`) into Prometheus,
`database.json`'s "hall of shame" panel visualizes it, and
`docs/grafana/alerts.yml` pages when a statement stays slow.

## Trigger

- **`SlowQuerySustained` fires** (`docs/grafana/alerts.yml`) — the intended
  entry point; see "What the alert means" below for what it does and doesn't
  claim.
- A statement climbs the "hall of shame" panel on `database.json` during a
  routine look, without having paged anyone.
- ADR-0003's replica trigger sent you here: sustained primary CPU that turns
  out to be one pathological query rather than read volume.

Not this runbook: request latency with no slow statement behind it (that is
the application — check `http_requests_inprogress` and the pool-utilization
panel first), or an N+1 caught in CI, which
`backend/tests/test_query_budgets.py` reports directly with the statements it
captured.

## Reading the dashboard

Open `database.json` (`make observe` → <http://localhost:3001>, or the
managed Grafana it's imported into) and look at two panels:

- **Hall of shame** (table): the top 10 statements by *total* execution time
  — `calls × mean_exec_time`. A statement can land here either because it's
  individually slow or because it's called constantly; the `calls` column
  disambiguates.
- **Mean execution time by statement** (timeseries, filtered to
  `mean_exec_time > 100ms`): shows whether a statement's average latency is
  trending up over time, or if it's just been consistently slow.

Both panels key statements by `queryid` — Postgres's stable identifier for a
normalized statement (literals stripped). The `query` label carries the
normalized text, truncated to 200 characters.

## What the alert means

`SlowQuerySustained` (`docs/grafana/alerts.yml`) fires when a statement's
**mean** execution time has stayed above 500ms for 15 minutes straight, and
it's been called more than 100 times in that window (a rarely-called
statement holding a stale high mean doesn't page anyone).

`pg_stat_statements` only exposes mean/max per statement — there's no true
percentile in this data. Treat this alert as "something is *consistently*
slow," not "p95 latency breached a number." For real end-to-end request p95
(the number users actually feel), check the k6 load-test suite (T-223)
instead — this alert is a leading indicator, not the SLO measurement itself.

## Pre-checks

- A `psql` session against the database the alert came from. Note that
  `EXPLAIN ANALYZE` **executes** the statement — on a write it writes, so wrap
  those in a transaction you roll back.
- Enough production-shaped data to plan against. A plan taken on a dev
  database with 40 rows will choose a sequential scan for correct reasons and
  tell you nothing; `make seed-load` (T-223) is the local stand-in.
- The `queryid` from the dashboard. Copying the truncated `query` text works
  too, but `queryid` is the stable identifier — the text is cut at 200
  characters.

## Steps

1. **Find the statement.** Either copy the truncated `query` label from the
   dashboard, or connect to Postgres directly and look it up by `queryid`:

   ```sql
   SELECT query, calls, mean_exec_time, total_exec_time, rows
   FROM pg_stat_statements
   WHERE queryid = 123456789012345;
   ```

2. **Get the real plan.** `pg_stat_statements` normalizes literals out of the
   query text, so you'll usually need to substitute representative values
   back in before running `EXPLAIN (ANALYZE, BUFFERS)`:

   ```sql
   EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
   SELECT * FROM invoice WHERE org_id = '<a real org uuid>' AND status = 'open';
   ```

   Look for: sequential scans on large tables, nested loops with a
   high-row-count inner side, sort/hash operations spilling to disk
   (`Sort Method: external merge`, `Batches: N` where `N > 1` on a hash
   join), and any row-count estimate wildly off from the actual (a stale
   statistics problem — `ANALYZE <table>` first, re-check before doing
   anything more invasive).

3. **Decide: index, rewrite, or cache.**
   - **Add an index** when the plan shows a sequential scan (or a wide
     index scan) on a predictable, selective filter/join column that
     doesn't already have one covering it. Verify the win with
     `EXPLAIN ANALYZE` before and after on a realistic data volume — an
     index that doesn't change the plan isn't worth the write-amplification
     cost of maintaining it.
   - **Rewrite the query** when the shape itself is the problem: an N+1
     pattern that should be a single join/`IN`, a `SELECT *` pulling columns
     nothing uses, an `OFFSET`-based pagination that gets slower per page
     (switch to keyset pagination), or a subquery Postgres can't flatten
     that a `JOIN`/CTE can.
   - **Cache** when the query is correct and already using the best
     available plan, but is called far more often than its underlying data
     changes (e.g. a plan/pricing lookup, an org's feature-flag set). Prefer
     an existing cache layer (Redis, already wired for sessions/rate
     limiting) over adding a new one.
   - Re-run `EXPLAIN ANALYZE` (or wait for the next `pg_stat_statements`
     window) after any fix to confirm `mean_exec_time` actually dropped —
     don't close the alert on a plausible-sounding fix alone.
   - **If the fix was an N+1**, add the endpoint to
     `backend/tests/test_query_budgets.py` (T-243) so it can't come back:
     that suite hits every list endpoint with 3 rows and again with 30 and
     fails if the statement count differs, printing every statement it
     captured. A new `list_`-style route with no budget entry there already
     fails its meta-check, so most N+1s should be caught in CI rather than
     from a `pg_stat_statements` alert.

4. **Reset the counters** after a fix ships, so the dashboard reflects new
   behavior instead of being dominated by pre-fix history:

   ```sql
   SELECT pg_stat_statements_reset();
   ```

## Verification

A fix is verified when the *dashboard* agrees, not when the plan does. After
the counter reset in step 4, wait for a real traffic window and check:

- the statement's `mean_exec_time` on the "Mean execution time by statement"
  panel is below the 500ms alert threshold and stays there;
- it has left the hall of shame, or has moved down it — a query called
  constantly can be individually fast and still top a `calls × mean` ranking,
  which is not a regression;
- `SlowQuerySustained` has resolved rather than being silenced.

The negative check: `pg_stat_statements_reset()` empties the table, so
*everything* looks healthy for the first few minutes afterwards. A green
dashboard immediately after a reset is evidence of the reset, not the fix.

If the fix was an index, confirm it is actually being used
(`EXPLAIN` shows the index scan on the real data volume, not just on your
sample) — an unused index costs write throughput and buys nothing.

### Local verification

Against the dev stack (`make dev` + `make observe`), after generating some
traffic:

```sql
-- from inside the postgres container, or any client pointed at it
SELECT count(*) FROM pg_stat_statements;
```

Should return a count greater than zero once the app has executed queries
with `shared_preload_libraries=pg_stat_statements` active (see
`docker-compose.yml`'s `postgres` service `command`) — if it's zero, check
`docker compose logs postgres` for a startup complaint about the preload
flag, not the extension itself (the migration only creates the catalog
objects; the flag is what actually turns on collection).

## Rollback

Everything in the investigation steps is read-only except `ANALYZE` (harmless,
and worth keeping) and `pg_stat_statements_reset()` (irreversible — you lose
the history, which is why step 4 comes *after* the fix ships, not before).

The fix itself rolls back like any other change:

- **An index**: `DROP INDEX CONCURRENTLY <name>;` — and revert the migration
  that added it, or the next environment gets it back. Use `CONCURRENTLY` in
  both directions on a live database; a plain `CREATE INDEX` takes a lock that
  blocks writes on the table for the duration.
- **A rewritten query**: revert the commit. Keep the `EXPLAIN ANALYZE` output
  from before and after in the PR — it is the only record of why the shape
  changed, and the next person to "simplify" it will need it.
- **A new cache**: revert, then re-check the invalidation matrix
  (`app/core/cache_keys.py`, T-240). A half-removed cache that still has
  writers invalidating a key nothing reads is the worse state to stop in.

## Escalation

- **The plan is already optimal and the query is still slow.** The problem is
  volume or hardware, not the statement. That is the point at which ADR-0003's
  read-replica triggers and ADR-0002's pooler sizing are the right
  conversation — both list their own cheaper fixes first.
- **A statement you don't recognise tops the list.** Check whether it is
  Alembic, the postgres-exporter's own scrape queries, or a background job
  before optimising something no user waits on.
- **Row-count estimates stay wrong after `ANALYZE`.** Autovacuum is likely not
  keeping up on that table — that is a database-tuning problem
  (`autovacuum_vacuum_scale_factor` and friends) rather than a query one, and
  it will come back on the next table if only this one is patched.
- **The alert fires repeatedly on different statements.** Stop fixing them
  one at a time; something shared moved — a missing index after a restore, a
  connection pool starving, or a replica/pooler misconfiguration.
