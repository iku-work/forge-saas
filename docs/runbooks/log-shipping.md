# Log shipping

T-215. Where logs and traces actually go today, and the explicit,
reasoned non-plan for centralizing them further. Closes ADR-0001's
V1.7.2 gap (V7.2.1 in `docs/security/owasp-top10.md`'s A09 row).

## 1. What exists today

Three independent output paths, none of which forward to each other:

| Stream | Destination | Purpose | Retention |
|---|---|---|---|
| Structured JSON logs | stdout/stderr (`backend/app/core/logging.py` `_json_sink`) | Every application log line — request lifecycle, background job output, third-party client noise routed through `_InterceptHandler` | Whatever the container runtime/hosting platform keeps (not managed by this repo) |
| Admin audit trail | `admin-audit.log`, a local file (`Settings.ADMIN_AUDIT_LOG_PATH`, default `admin-audit.log`) | Superadmin actions only (`admin_action=True` in the log's `extra`) — impersonation, plan overrides, etc. | Append-only file on the container's local disk — lost on container recreation unless the platform mounts a persistent volume |
| Errors/exceptions | Sentry (`backend/app/core/sentry.py`, `SENTRY_DSN`) | Unhandled exceptions + explicitly captured ones (`sentry_sdk.capture_exception` in `handlers.py`, `audit/service.py`, and several worker jobs) | Sentry's own retention policy |
| Distributed traces | OTLP endpoint, if configured (`backend/app/core/tracing.py`, `OTEL_EXPORTER_OTLP_ENDPOINT`) | Request/job spans, correlatable with log lines via `trace_id`/`span_id` (`logging.py`'s `_active_trace_ids()`) | Whatever the OTLP collector (Tempo/Jaeger/Honeycomb/etc.) is configured to keep — not this repo's concern once the endpoint is set |

All four share the same PII/secret scrubber
(`backend/app/core/logging.py`'s `_scrub()`, reused directly by
`sentry.py`'s `_before_send`/`_before_breadcrumb`) — there is one
redaction policy, not one per sink.

## 2. What doesn't exist

- **No centralized log aggregation / SIEM.** stdout logs go wherever the
  hosting platform's log capture sends them (e.g. Render's log stream,
  `docker logs`) — there's no confirmed pipeline shipping them to a
  queryable store like Loki, Elasticsearch, or a SIEM product.
- **`admin-audit.log` isn't shipped anywhere.** It's a local file. If the
  container is recreated without a persistent volume for it, its history
  is gone. It is *not* the same thing as the `AuditEvent` DB table
  (`backend/app/modules/audit/`), which is durable, queryable, and the
  actual source of truth for the in-app audit UI — `admin-audit.log` is a
  secondary, file-based trail for the same events, useful for
  grep/tail-style investigation without a DB round-trip.
- **No alerting pipeline beyond Sentry's own.** Rate-limit 429s and
  permission-denied 403s aren't fed into an anomaly-detection system
  (tracked as T-225/V8.1.4, not this document).

## 3. The plan (and why it isn't "just add a log shipper" today)

This is deliberately left as a hosting-platform decision, not a
repo-level default, because:

1. **The right destination depends on where the app is deployed.**
   Render, Fly, a self-managed VPS, and a Kubernetes cluster each have a
   different idiomatic answer (native log drains, a Vector/Fluent Bit
   sidecar, a cloud provider's log service). Hardcoding one into the
   Docker image would be the wrong default for most of them.
2. **OTLP tracing already gives a vendor-neutral path for spans**
   (`tracing.py`), and the same is true in spirit for logs: `_json_sink`
   already emits structured JSON to stdout, which is the input format
   every mainstream log shipper (Vector, Fluent Bit, Promtail) expects
   with zero transformation. The integration point already exists; only
   the shipper + destination are undecided.
3. **`admin-audit.log`'s durability gap is a real, open follow-up**, not
   an accepted permanent gap: mount it on a persistent volume (or drop
   the file sink and rely solely on the `AuditEvent` DB table, which is
   already durable) the next time infra work touches log/volume
   configuration. Tracked as part of T-225's build/infra hardening batch.

**When a team actually deploys this template to a specific platform**,
the concrete next step is: run one of the JSON-native log shippers above
as a sidecar/log-drain pointed at stdout, forward to whatever the team's
existing observability stack is (or stand up Loki/Grafana alongside the
existing OTLP setup if there isn't one yet), and update this document
with the real destination once it exists. Until that happens, "logs are
on stdout + Sentry has exceptions + traces are OTLP if configured" is the
accurate, honest answer — this document exists so that answer is written
down instead of assumed.

## 4. Operational notes

- To find a specific request across all three streams: grab its
  `request_id` (set by `backend/app/middleware/request_id.py`, present
  as a response header and in every log line for that request) or its
  `trace_id`/`span_id` if OTLP is configured, and grep/query for it in
  each destination.
- Sentry `send_default_pii=False` (`sentry.py`) plus the shared `_scrub()`
  pass mean Sentry events are safe to grant broader team access to than
  raw stdout logs might be — the scrubbing is defense in depth, not a
  reason to skip access control on either.
