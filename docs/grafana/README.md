# Grafana dashboards (T-204)

Dashboards are code: the JSON in `dashboards/` is the source of truth, not
whatever's live in any particular Grafana instance. Every dashboard picks its
datasource through a `DS_PROMETHEUS` template variable (`type: datasource`,
`query: prometheus`) — no panel ever hardcodes a datasource UID, so the same
JSON imports cleanly into any Grafana instance that has *a* Prometheus
datasource configured, whatever it happens to be named or scoped to.

## Dashboards

| File               | Covers                                                                 |
| ------------------- | ----------------------------------------------------------------------- |
| `api-overview.json` | Request rate, 4xx/5xx error rate, p50/p95/p99 latency, in-flight requests, all by route |
| `workers.json`      | ARQ queue depth, emails/push sent. Per-job duration/failure-rate panels are deferred — see the note panel in the dashboard itself |
| `database.json`     | Connection pool usage vs size, pool utilization, `pg_stat_statements` "hall of shame" (top 10 slow statements) and mean-exec-time-by-statement (T-205). Connection wait panel stays deferred — no metric emitted |
| `business.json`     | Active sessions, signups/day, Stripe webhook processing time (p50/p95) |

Most panels query metric names from `backend/app/core/metrics.py` (T-202) and
the `prometheus-fastapi-instrumentator` defaults (`http_requests_total`,
`http_request_duration_seconds_bucket`, `http_requests_inprogress`) —
`database.json`'s hall-of-shame panels are the one exception, querying
`pg_stat_statements_*` metrics emitted by a separate
`prometheuscommunity/postgres-exporter` scrape target instead (see
"Slow-query observability" below). Nothing here queries a metric neither of
those two exporters actually emits.

## Slow-query observability (T-205)

`postgres-exporter-queries.yaml` is a custom-queries file for
`prometheuscommunity/postgres-exporter`, exposing the top 50
`pg_stat_statements` rows by total execution time as `pg_stat_statements_calls`
/ `_mean_exec_time` / `_total_exec_time` / `_rows` metrics (labeled by
`queryid`, truncated `query` text, and `user`). `alerts.yml` fires
`SlowQuerySustained` when a statement's mean execution time stays above
500ms for 15 minutes across 100+ calls — see `docs/runbooks/slow-queries.md`
for how to read and act on both. `prometheus.yml` scrapes the exporter as the
`forge-postgres` job and loads `alerts.yml` via `rule_files`.

This requires two things beyond the exporter itself: the `pg_stat_statements`
extension (created by `backend/alembic/versions/core/
a1c9f3e7b2d4_pg_stat_statements.py`, part of the always-on core migration
trunk) and the library preloaded at Postgres startup (`shared_preload_libraries=
pg_stat_statements` in both `docker-compose.yml` and `docker-compose.prod.yml`'s
postgres `command` — the migration alone only registers the catalog objects,
it doesn't turn on collection).

`alerts.yml` is validated the same way as the dashboards: with `promtool`,
via `backend/tests/test_prometheus_alerts.py` (part of the normal
`pytest --cov=app` CI run, same pattern as the dashboard-JSON checks below).

## Viewing locally with live data

```
make observe
```

Starts `prometheus` + `postgres-exporter` + `grafana` on the `observability`
Compose profile (never started by `make dev`). Prometheus scrapes
`backend:8000/metrics` and `postgres-exporter:9187/metrics` per
`prometheus.yml`, and loads `alerts.yml`; Grafana auto-provisions the
Prometheus datasource from `provisioning/` and all four dashboards from
`dashboards/` (mounted as a single directory bind — see the comment in
`docker-compose.yml`'s `grafana` service for why individual per-file mounts
don't work here). Open
<http://localhost:3001> (anonymous admin access — this stack is dev-only and
never exposed outside the Compose network plus the one published port).

Tear down with `make down` (stops the whole stack, including this profile)
or `docker compose --profile observability down`.

## Importing into a managed Grafana

Dashboards → New → Import → upload the JSON file (or paste it). Grafana
detects the `DS_PROMETHEUS` variable and prompts you to pick which of your
existing Prometheus datasources it should map to — select it and import.

## Editing a dashboard and committing the result

1. Edit the dashboard in the Grafana UI (either the imported copy or the one
   from the local `make observe` stack).
2. Dashboard settings → JSON Model → copy the JSON.
3. Before committing, reconcile it back to this directory's conventions:
   - `"id"` must be `null` (Grafana assigns a numeric id per-instance; a
     committed non-null id from your instance would collide with a
     different id on someone else's).
   - `"uid"` stays the stable value already in this repo (e.g.
     `forge-api-overview`) — don't let Grafana's auto-generated uid replace
     it.
   - The `templating.list` entry for `DS_PROMETHEUS` and every panel/target
     `"datasource"` must still read `{"type": "prometheus", "uid":
     "${DS_PROMETHEUS}"}` — Grafana rewrites these to your instance's real
     datasource uid the moment you save inside the UI, so diff carefully
     and revert any panel that got a hardcoded uid before committing.
4. Save over the matching file in `dashboards/`. CI
   (`backend/tests/test_grafana_dashboards.py`) fails the build if any of
   the above regresses.
