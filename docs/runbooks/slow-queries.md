# Slow-query runbook (T-205)

Postgres's `pg_stat_statements` extension tracks per-normalized-statement
timing across the whole server. `prometheuscommunity/postgres-exporter`
scrapes it (`docs/grafana/postgres-exporter-queries.yaml`) into Prometheus,
`database.json`'s "hall of shame" panel visualizes it, and
`docs/grafana/alerts.yml` pages when a statement stays slow.

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

## Investigation workflow

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

## Local verification

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
