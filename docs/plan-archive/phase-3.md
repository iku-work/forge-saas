<!-- Archived from AGENTS.md on 2026-09-30. Task specs and their completion
notes, kept verbatim as the record of what each task asked for and what
was found doing it. The laws in AGENTS.md §0 still bind. -->

# Phase 3 — Hardening

> HTML ref: §"Build phases" → "Phase 3"
>
> **Prerequisite:** T-160 (Phase 2 acceptance runbook) is complete and green. Do not start Phase 3 until every Phase 2 acceptance path passes without intervention.
>
> **Phase 3 acceptance criterion:** (1) An error thrown on backend, web, or mobile lands in Sentry with a request ID; a request can be followed from HTTP span → DB span → job span in traces; Grafana dashboards render from committed JSON. (2) ZAP baseline scan, OWASP Top 10 test mapping, and the ASVS L2 checklist are green in CI. (3) The full signup → verify → invite → upgrade flow passes in Playwright on Chromium + WebKit, in Maestro on mobile, under k6 load within latency budgets, and with zero axe-core violations. (4) The app ships in five+ locales including one RTL locale. (5) No list endpoint issues N+1 queries and no route exceeds its bundle budget. (6) `make new-module NAME=foo` produces a module that passes lint + mypy out of the box. Each item has an automated check — Phase 3 "done" is a green pipeline, not a claim.

---

## P3-A · Observability (full)

> HTML ref: §"Observability" (entire section)
>
> The three pillars — logs (done in T-011), metrics, traces — plus error tracking and resilience. Rule of thumb: every incident postmortem question ("what happened, to whom, how slow, which dependency") must be answerable from a dashboard, not from a shell session.

### T-201 Sentry SDK wired on backend, web, and mobile

> HTML ref: §"Observability" → "Request ID middleware" (Sentry tag integration)
> needs: T-002, T-040, T-045

**Goal** Errors on all three platforms report to Sentry with release, environment, and request ID — and never contain PII or secrets.

**Build**

- Backend — `backend/app/core/sentry.py`, called from the app factory **before** middleware registration:
  - `sentry_sdk.init(dsn=settings.SENTRY_DSN, environment=settings.ENVIRONMENT, release=app_version, send_default_pii=False, traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE)`
  - Init is a no-op when `SENTRY_DSN` is unset (dev default) — the rest of the app must not care
  - `before_send` hook reuses the T-011 scrubber key list (`password`, `token`, `secret`, `api_key`, `authorization`, `cookie`) to redact event payloads and breadcrumbs
  - New settings: `SENTRY_TRACES_SAMPLE_RATE: float = 0.1` (+ `.env.example` entry)
  - Note: the global handler (T-006) already calls `sentry_sdk.capture_exception` and T-007 sets the `request_id` tag — with the SDK initialised those now actually deliver. Verify both still work; do not double-report (the FastAPI integration also captures unhandled exceptions — configure the handler to only capture what the integration misses, or disable the integration's duplicate capture).
- Web — `apps/web/src/lib/sentry.ts`:
  - `@sentry/react` init gated on `import.meta.env.VITE_SENTRY_DSN`; `environment` + `release` from build-time env
  - Top-level `<ErrorBoundary>` with a branded fallback page ("Something went wrong · Reload") — never a white screen
  - Source maps uploaded via the Sentry Vite plugin, CI builds only (auth token from CI secret; plugin disabled locally)
- Mobile — `apps/mobile/`:
  - `@sentry/react-native` with the Expo config plugin; init in the root layout, gated on env
  - Release tagging compatible with OTA updates (`release` = app version + update ID) so stack traces map to the right bundle
- All three: user context set to `{ id: user_id }` only — never email or name

**Tests**

- Backend: `before_send` given an event containing `"password": "hunter2"` in extra → value replaced with `"***"`
- Backend: init with `SENTRY_DSN=None` → app boots, no SDK calls fail
- Backend: endpoint raising `RuntimeError` → exactly one Sentry event captured (assert via `sentry_sdk` test transport), tagged with `request_id`
- Web: rendering a component that throws inside the boundary shows the fallback page (Vitest + RTL)

**Done when**

- [ ] A thrown test error on each platform appears in Sentry (verify once against a real DSN, then rely on transport tests)
- [ ] Scrubber test passes — no secret keys in any event
- [ ] Source maps resolve web stack traces in the Sentry UI
- [ ] `mypy --strict` / `tsc --strict` clean

---

### T-202 Prometheus metrics — `/metrics` on internal network only

> HTML ref: §"Observability" → "Metrics (Prometheus)" | "Health endpoints"
> needs: T-002

**Goal** RED metrics (rate, errors, duration) for every endpoint plus app-specific gauges, scrapeable inside the Docker network but unreachable from the public edge.

**Build**

- `backend/app/core/metrics.py`:
  - `prometheus-fastapi-instrumentator` (already pinned in T-002 deps): `Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)`
  - Custom collectors, registered here and updated where the data lives:
    - `forge_db_pool_used` / `forge_db_pool_size` gauges (read from `engine.pool.checkedout()` / `.size()`)
    - `forge_arq_queue_depth` gauge (Redis `LLEN` on the ARQ queue key, sampled by a collector callback)
    - `forge_active_sessions` gauge (count of non-revoked, non-expired `Session` rows — refreshed by a 60 s APScheduler job, not per-scrape)
    - `forge_stripe_webhook_seconds` histogram — wrap the T-030 webhook handler
    - `forge_emails_sent_total` / `forge_push_sent_total` counters in the respective jobs
- Edge lockout — `/metrics` must never be public:
  - `Caddyfile` and `Caddyfile.prod`: `respond /metrics 404` before the `reverse_proxy` directive
  - `docker-compose.prod.yml`: backend port stays internal to `forge-net`; only Caddy publishes ports
- Metric label discipline: route templates (`/api/v1/users/{id}`), never raw paths — no per-UUID label cardinality explosions

**Tests**

- Direct ASGI request to `/metrics` → 200, `text/plain` Prometheus format, contains `forge_db_pool_size`
- Request through Caddy to `https://localhost/metrics` → 404 (integration, dev compose)
- CI grep: both Caddyfiles contain the `respond /metrics 404` rule (same pattern as the T-004.5 redirect grep)
- Stripe webhook test asserts the histogram observed a sample

**Done when**

- [ ] `/metrics` scrapeable from inside `forge-net`, 404 via Caddy
- [ ] All five custom metric families present in output
- [ ] No high-cardinality labels (assert label values for the request metric are route templates)

---

### T-203 OpenTelemetry auto-instrumentation — FastAPI + SQLAlchemy + ARQ + Redis

> HTML ref: §"Observability" → "Traces (OpenTelemetry)"
> needs: T-002, T-011

**Goal** One request produces one trace: HTTP server span → DB query spans → Redis spans; background jobs produce their own root spans. Exporter is vendor-neutral and switched by a single env var.

**Build**

- `backend/app/core/tracing.py`, called from the app factory:
  - `TracerProvider` with resource attrs `service.name="forge-backend"`, `service.version`, `deployment.environment`
  - OTLP exporter configured from standard `OTEL_EXPORTER_OTLP_ENDPOINT` env var; when unset, tracing is a no-op (dev default) — document that Tempo, Jaeger, and Honeycomb all speak OTLP
  - Parent-based ratio sampler, rate from `OTEL_TRACES_SAMPLER_ARG` (default `0.1`)
  - Instrument: `FastAPIInstrumentor`, `SQLAlchemyInstrumentor` (on the async engine's sync core), `RedisInstrumentor`
- ARQ has no official instrumentation — wrap job execution in the worker (T-034's `WorkerSettings.on_job_start` / custom runner): span name `arq.<job_name>`, attributes `job.id`, `job.try`, `job.queue`
- Log/trace correlation: inject `trace_id` + `span_id` into Loguru records (extend the T-011 configuration) so a log line can be pasted into the trace viewer
- `request_id` (T-007) set as a span attribute on the server span

**Tests**

- Using the in-memory span exporter: one API call that reads the DB → a server span with ≥1 child span of kind CLIENT with `db.system="postgresql"`
- Enqueue + run a test ARQ job → root span `arq.<name>` with the job attributes
- `OTEL_EXPORTER_OTLP_ENDPOINT` unset → no exporter configured, app boots, zero overhead beyond no-op tracer
- A log record emitted inside a traced request contains the active `trace_id`

**Done when**

- [ ] Span tree test passes (HTTP → DB parent/child relationship asserted)
- [ ] ARQ job spans emitted
- [ ] Trace/log correlation test passes
- [ ] `mypy --strict` clean

---

### T-204 Grafana dashboards exported as JSON

> HTML ref: §"Observability" → "Metrics (Prometheus)"
> needs: T-202

**Goal** Dashboards are code: committed JSON that any Grafana instance can import, plus a local compose profile to view them during development.

**Build**

- `docs/grafana/` dashboards (datasource as a `${DS_PROMETHEUS}` template variable, never a hardcoded UID):
  - `api-overview.json` — request rate, error rate (4xx/5xx split), p50/p95/p99 latency by route, in-flight requests
  - `workers.json` — ARQ queue depth, job duration by name, job failure rate, emails/push sent
  - `database.json` — pool usage vs size, connection wait, (slow-query panels land in T-205)
  - `business.json` — active sessions, signups/day, webhook processing time
- Optional local stack — `docker-compose.yml` profile `observability`:
  - `prom/prometheus` scraping `backend:8000/metrics` (config in `docs/grafana/prometheus.yml`)
  - `grafana/grafana` with provisioning mounts auto-loading the four dashboards + datasource
  - `make observe` target: `docker compose --profile observability up -d`
- README in `docs/grafana/` — how to import into a managed Grafana, how to regenerate JSON after editing (export → strip `id`/`uid` → commit)

**Tests**

- CI script: every `docs/grafana/*.json` parses, declares `${DS_PROMETHEUS}`, and contains no hardcoded datasource UID
- Smoke (documented, run once locally): `make observe` → Grafana at `:3001` renders all four dashboards with live data

**Done when**

- [ ] Four dashboards committed and CI-validated
- [ ] `make observe` shows live request data end-to-end
- [ ] README documents the export-and-commit loop

---

### T-205 Postgres slow-query observability — `pg_stat_statements`

> HTML ref: §"Observability" → "Metrics (Prometheus)" | §"Best practices" → "Database best practices"
> needs: T-202, T-204

**Goal** The slowest statements in the system are visible on a dashboard and alert before users complain.

**Build**

- Enable the extension:
  - `docker-compose.yml` + `docker-compose.prod.yml`: postgres `command: -c shared_preload_libraries=pg_stat_statements -c pg_stat_statements.track=all`
  - Migration: `op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_stat_statements"))` — raw SQL justified per §0.3 (DDL not expressible in SQLAlchemy); `downgrade()` drops the extension
- `prometheuscommunity/postgres-exporter` service (compose `observability` profile + prod):
  - Custom queries file `docs/grafana/postgres-exporter-queries.yaml` exporting per-statement `calls`, `mean_exec_time`, `total_exec_time`, `rows` for the top 50 statements by total time
  - Note honestly: `pg_stat_statements` exposes mean/max, not true p95 — the p95 alert target from the plan is implemented as **mean > 500 ms sustained**, with the k6 suite (T-223) owning true end-to-end p95
- Prometheus alert rule file `docs/grafana/alerts.yml`: statement with `calls > 100` and `mean_exec_time > 500ms` for 15 min → warning; validated with `promtool check rules` in CI
- "Hall of shame" panel added to `database.json` (T-204): top 10 statements by total time, with query text
- Runbook `docs/runbooks/slow-queries.md`: how to read the dashboard, `EXPLAIN ANALYZE` workflow, when to add an index vs rewrite vs cache

**Tests**

- Migration round-trip (extension created and dropped)
- Integration: after running the test suite against the dev stack, `SELECT count(*) FROM pg_stat_statements` > 0
- CI: `promtool check rules docs/grafana/alerts.yml` exits 0

**Done when**

- [ ] Extension live in dev + prod compose
- [ ] Exporter metrics visible in Prometheus; hall-of-shame panel renders
- [ ] Alert rule validated in CI; runbook written

---

### T-206 Circuit breakers on all external calls

> HTML ref: §"Best practices" → "Error handling" (degraded modes)
> needs: T-002, T-030, T-035, T-111, T-113

**Goal** A slow or dead third party (Stripe, Resend, S3, virus scanner, Expo push) degrades one feature — it never cascades into request-path timeouts or a worker pile-up.

**Build**

- `backend/app/core/resilience.py`:

  ```python
  # One named breaker per integration, module-level registry
  BREAKERS: dict[str, pybreaker.CircuitBreaker] = {
      "stripe": ..., "resend": ..., "s3": ..., "virus_scanner": ..., "expo_push": ...,
  }  # fail_max=5, reset_timeout=30s

  def resilient(integration: str, *, retries: int = 3) -> Callable:
      """Decorator: tenacity retry (exponential 0.5s→4s, retry ONLY on
      transient errors — timeouts, connection errors, 5xx) wrapped inside
      the integration's circuit breaker. Open breaker raises
      ExternalServiceUnavailableError without attempting the call."""
  ```

- New exception `ExternalServiceUnavailableError(DomainError)`: `status_code=503`, `code="service_unavailable"`, message names the degraded feature, not the vendor internals
- Retry discipline: never retry non-idempotent calls without an idempotency key — §0.4 already mandates keys on Stripe, webhooks, and email sends; assert the decorator is only applied to call sites that pass one
- Apply to every outbound call site: Stripe API calls (T-031), Resend send (T-035), S3/R2/MinIO operations (T-111), virus scanner (T-113), Expo push (T-105)
- Metrics (registered via T-202): `forge_breaker_state{integration}` gauge (0 closed / 1 half-open / 2 open), `forge_breaker_open_total{integration}` counter, `forge_external_call_seconds{integration}` histogram
- Degraded-mode behaviour, implemented and documented in `docs/runbooks/degraded-mode.md`:
  - **Stripe down** — checkout/portal endpoints return 503 envelope; webhooks are safe (Stripe retries for days)
  - **Resend down** — email jobs re-queue with ARQ backoff; emails delayed, never dropped
  - **S3 down** — presign/download return 503; existing metadata unaffected
  - **Virus scanner down** — uploads stay `pending_scan` and undownloadable; scan job retries
  - **Expo push down** — push dropped after retries; in-app + email channels unaffected

**Tests**

- 5 consecutive simulated failures → breaker opens → 6th call raises `ExternalServiceUnavailableError` **without** invoking the wrapped function
- After `reset_timeout` (freezegun / injected clock) → half-open → one success closes it
- Retry fires on `httpx.TimeoutException`, does **not** fire on a 400 response
- Open `resend` breaker → email job re-queued, not marked failed
- Metrics reflect open/close transitions

**Done when**

- [ ] All five integrations wrapped; grep test asserts no bare outbound `httpx`/SDK call outside `resilience`-decorated call sites
- [ ] Breaker lifecycle tests pass, 100% coverage on `resilience.py`
- [ ] Degraded-mode runbook covers all five integrations

---

## P3-B · OWASP hardening

> HTML ref: §"OWASP & security testing" (entire section)
>
> Security is verified continuously, not audited once. T-054 already runs SAST (Bandit, Semgrep), dependency scans, and secrets scanning. This section adds DAST, proves the OWASP Top 10 mapping with tests, and produces the documents a real engagement needs.

### T-210 OWASP ZAP baseline scan in CI against an ephemeral deploy

> HTML ref: §"OWASP & security testing" → "DAST — OWASP ZAP"
> needs: T-055

**Goal** Every PR gets a passive DAST pass against a real running instance — missing headers, cookie flags, and info leaks fail the build before review.

**Build**

- CI job `zap-baseline` in the security workflow (needs the compose stack, so runs after build):
  - Boot the ephemeral stack: `docker compose up -d` (backend + Caddy + Postgres + Redis + MinIO + Mailhog), seed via `make seed`, wait for `/health/ready`
  - Run `ghcr.io/zaproxy/zaproxy:stable zap-baseline.py -t https://caddy -c .zap/baseline.conf -r zap-report.html` on the compose network (trusting the dev cert)
  - Fail the job on any FAIL; WARNs allowed only if listed in `.zap/baseline.conf` with a dated justification comment
  - Upload `zap-report.html` as a workflow artifact
- `.zap/baseline.conf` starts empty except documented ignores (e.g. Mailhog UI if scanned) — every ignore has an owner and reason
- Scan scope: the app origin only; explicitly exclude `/metrics` (returns 404 via Caddy anyway, per T-202)

**Tests**

- One-time verification on a throwaway branch: comment out the `security_headers` middleware → ZAP job fails (proves the gate bites); revert
- Steady state: job green on `main`

**Done when**

- [ ] `zap-baseline` job green in CI on a real PR
- [ ] Header-removal canary run demonstrated failure once (link the red run in the PR description)
- [ ] Report artifact downloadable from the workflow run

---

### T-211 Nightly ZAP full active scan against staging

> HTML ref: §"OWASP & security testing" → "DAST — OWASP ZAP"
> needs: T-210

**Goal** The aggressive scan (active attacks, fuzzing) runs where it can't hurt anyone: nightly, against staging, authenticated.

**Build**

- `.github/workflows/zap-full.yml`: `schedule: cron "0 3 * * *"` + `workflow_dispatch`
- ZAP Automation Framework plan (`.zap/full-scan.yaml`):
  - Target: staging URL from a repo variable
  - Authentication: dedicated `zap-scanner@<staging-domain>` user (MEMBER role, own org, synthetic data) logging in via the real `/api/v1/auth/login` flow; session token refreshed by the plan
  - Active scan policy: default minus DoS-category rules (staging is shared)
  - Out of scope: Stripe-hosted pages, Mailhog, `/metrics`, GDPR delete endpoint for the scanner's own account (it would lock itself out)
- Findings handling: new alerts above LOW open a GitHub issue (one per alert type, deduped by fingerprint) labeled `security`; report artifact retained 90 days
- Scanner credentials rotated by the same process as other staging secrets; documented in the workflow README header

**Tests**

- `workflow_dispatch` run completes end-to-end: authenticates (assert ≥1 authenticated request in the report), scans, uploads report
- Deduplication: re-running against unchanged staging opens no duplicate issues

**Done when**

- [ ] Nightly run green (or red with real findings triaged as issues)
- [ ] Authenticated coverage confirmed in the report
- [ ] Runbook note: what to do when the nightly scan finds something (severity → SLA from T-214)

---

### T-212 OWASP Top 10 test mapping — verified in CI

> HTML ref: §"OWASP & security testing" → "OWASP Top 10 — mitigation & test mapping"
> needs: T-049

**Goal** The claim "every OWASP Top 10 risk has a test proving its mitigation" becomes machine-checked, not a table in a document that drifts.

**Build**

- `docs/security/owasp-top10.md`: the mapping table from the HTML plan (A01–A10 → mitigation in this codebase → test file), kept human-readable
- Pytest marker: `@pytest.mark.owasp("A01")` applied to the existing tests that prove each mitigation:
  - A01 broken access control → T-049 cross-tenant sweep + RBAC matrix (T-021)
  - A02 crypto failures → T-011 log-scrubber tests, T-007 HSTS header test, T-009.5 encryption tests
  - A03 injection → T-050 Schemathesis run + Semgrep raw-SQL rule (CI presence check)
  - A04 insecure design → T-008/T-022 rate-limit tests, T-018 lockout tests
  - A05 misconfiguration → T-007 header tests, T-003 wildcard-CORS startup guard
  - A06 vulnerable components → CI presence check: T-054 audit jobs exist and are non-optional
  - A07 auth failures → T-019 rotation + theft detection, T-024 TOTP, T-018 lockout
  - A08 integrity failures → T-030 webhook bad-signature test, lockfile-unchanged CI check
  - A09 logging failures → T-028 audit-row-on-sensitive-action tests
  - A10 SSRF → **new code**: `backend/app/core/url_guard.py` `validate_outbound_url(url)` rejecting private ranges (RFC 1918, loopback, link-local, `169.254.169.254` metadata), non-http(s) schemes, and DNS-rebinding via resolved-IP check — built now, consumed by Phase 4 outbound webhooks (T-32x)
- CI step `owasp-map-check`: script asserts every category A01–A10 has ≥1 **passing** test carrying its marker (`pytest -m owasp` + a collect step that fails on a missing category); also fails if `owasp-top10.md` references a test file that no longer exists

**Tests**

- `url_guard` unit tests: `http://localhost`, `http://10.0.0.1`, `http://169.254.169.254/latest/meta-data`, `file:///etc/passwd`, hostname resolving to a private IP → all rejected; `https://example.com` accepted
- Meta-test: removing a marker from any category makes `owasp-map-check` fail (verified once on a branch)

**Done when**

- [ ] All 10 categories carry passing marked tests; CI check green
- [ ] `url_guard.py` at 100% coverage
- [ ] `docs/security/owasp-top10.md` cross-checked by CI

---

### T-213 ASVS L2 checklist — `docs/adr/0001-security-baseline.md`

> HTML ref: §"OWASP & security testing" → "Beyond the Top 10"
> needs: T-212

**Goal** A written, evidence-linked security baseline: which ASVS Level 2 requirements the template meets, which it deliberately doesn't, and why.

**Build**

- `docs/adr/0001-security-baseline.md` in ADR format (context / decision / consequences), containing the checklist:
  - One row per applicable ASVS 4.0 L2 requirement across chapters V1 (architecture), V2 (auth), V3 (sessions), V4 (access control), V5 (validation), V6 (crypto), V7 (logging), V8 (data protection), V9 (communications), V12 (files), V13 (API), V14 (config)
  - Columns: requirement · status (`met` / `partial` / `n/a`) · evidence — a repo-relative link to the code, test, or config that satisfies it
  - `n/a` rows carry one-line reasons (e.g. V11 business logic — product-specific, template documents the pattern only)
  - `partial` rows each name a follow-up task ID (existing or newly filed at the bottom of this document)
- This is the document referenced by T-214's pentest scope and by enterprise security questionnaires — write it for an external reader

**Tests**

- CI link-check script: every evidence link in the checklist resolves to an existing file (same mechanism as T-212's doc check)

**Done when**

- [ ] Every applicable L2 requirement has a row; zero rows with empty status
- [ ] Every `met` row links real evidence; every `partial` row names a follow-up
- [ ] Link-check green in CI

---

### T-214 Pentest readiness runbook

> HTML ref: §"OWASP & security testing" → "Penetration testing before real money"
> needs: T-210, T-211, T-213

**Goal** When a manual security engagement happens, day 1 is spent finding bugs — not asking basic questions.

**Pentest guidance** — read before commissioning any external security engagement. Pentesting is not one thing; pick the right tier for your stage:

| Trigger                                                                | Recommended tier                                                                                             | Rough cost      |
| ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ | --------------- |
| First deployment, no paying customers yet                              | Automated CI tooling only (already in the plan: T-054, T-210, T-211, T-049, T-050). No external engagement.  | £0              |
| Real users, want ongoing scrutiny                                      | Private bug bounty on HackerOne or Intigriti, scoped to auth + billing + file access                         | £500–£3000 / yr |
| Pre-enterprise sales or pre-fundraise                                  | Time-boxed pentest from a boutique firm (3–5 days)                                                           | £3000–£6000     |
| Enterprise procurement requires a report; regulated data; annual audit | Full formal pentest from a reputable firm (2–4 weeks) with executive summary suitable for sharing            | £8000–£25,000   |
| Handling actual payment cards                                          | Not applicable — the template uses Stripe Checkout (hosted). Cards never touch your infrastructure.          |                 |

**Build**

- `docs/runbooks/pentest-prep.md` containing:
  - **Scope document** — in scope: `/api/v1/*`, web app, mobile app; out of scope: Stripe-hosted pages, S3 storage internals, third-party integrations
  - **Test account provisioning** — a `make pentest-accounts` seed target creating one staging account per role (OWNER, ADMIN, MEMBER, superadmin) with realistic data volumes; credentials delivered out-of-band, rotated after the engagement
  - **Sensitive-endpoint inventory** — auth flows, payment endpoints, admin routes, impersonation (T-142/T-260/T-261), GDPR export/delete — generated from the OpenAPI schema, tagged
  - **Known-issues list** — anything already known (link the `security`-labeled issues from T-211) so testers don't waste time re-discovering it
  - **Staging environment details** — separate from prod, synthetic data only, snapshot/restore procedure
  - **Communication channel** — dedicated contact + escalation path for critical findings
  - **Remediation SLA policy** — critical = 24 h, high = 7 d, medium = 30 d, low = next release
  - **Report handling** — findings tracked to closure as issues, closure evidence linked, retest confirmation recorded
- Cross-link: ASVS checklist (T-213) attached as the baseline claim the testers try to break

**Tests**

- `make pentest-accounts` creates the four accounts on a fresh staging seed, idempotently (documentation-level verification; no CI gate beyond the target existing)

**Done when**

- [ ] Runbook complete — someone unfamiliar with the project could brief a pentest firm from it alone
- [ ] `make pentest-accounts` works against staging
- [ ] SLA policy agreed and written down

---

### T-215 Secure-SDLC & architecture documentation

> Filed by: T-213's ASVS checklist (`docs/adr/0001-security-baseline.md`) — closes rows V1.1.1, V1.1.2, V1.1.4, V1.1.5, V1.1.7, V1.5.1, V1.6.1, V1.7.2, V1.14.4
> needs: T-213

**Goal** Turn the architecture knowledge that currently only exists as prose in `agents.md` and scattered docstrings into the standalone docs ASVS V1 (Architecture, Design and Threat Modeling) expects to exist on their own.

**Build**

- `docs/security/sdlc.md` — the secure-SDLC policy: where security fits in planning/review/release. References the CI security gates already enforced (bandit/semgrep/trivy/pip-audit/gitleaks/trufflehog/ZAP) rather than inventing a new process on top of them
- `docs/security/threat-model.md` — trust boundaries + a data-flow diagram: client ↔ Caddy ↔ backend ↔ Postgres/Redis/object storage/Stripe/Resend/OAuth providers
- `docs/security/key-management.md` — lifecycle for `ENCRYPTION_KEY`/`JWT_PRIVATE_KEY`/`JWT_PUBLIC_KEY`: generation, rotation procedure (ties into the existing `ENCRYPTION_KEY_OLD`/`MultiFernet` support), who holds them
- `docs/runbooks/log-shipping.md` — where logs go beyond stdout/`admin-audit.log`/Sentry today, and the plan (or explicit non-plan) for a centralized remote sink
- Update `docs/adr/0001-security-baseline.md`: flip the rows listed above from `partial` to `met`, evidence links pointing at these new docs

**Tests**

- `backend/tests/security/test_security_baseline.py`'s link-check already covers `docs/...` paths — no test changes needed unless a new doc lives outside `docs/`/`backend/`/`.github/`

**Done when**

- [ ] All four docs exist and are cross-linked from `0001-security-baseline.md`
- [ ] ADR rows updated, link-check still green

---

### T-216 Auth/session UX gaps

> Filed by: T-213's ASVS checklist — closes rows V2.1.2, V2.1.5, V2.1.6, V2.1.8, V2.1.9, V2.1.12, V2.2.3, V2.5.5, V3.2.3, V3.3.4, V3.4.4, V3.5.1, V8.2.2, V8.2.3
> needs: T-213

**Goal** Close the concrete auth/session gaps the ASVS pass found. Most are small; the standout is that there's currently no way to change your password while logged in — only a forgot-password reset flow.

**Build**

- `POST /api/v1/auth/change-password` (current + new password; current re-verified via Argon2; same password-policy validation as registration; revokes other sessions on success, same as password reset)
- `password: str = Field(min_length=12, max_length=128)` on every password-accepting schema (currently has no upper bound)
- Reconsider `_validate_password_strength`'s upper/lower/digit composition requirement against NIST SP 800-63B / ASVS V2.1.9 (which explicitly recommends against composition rules in favor of length + breach-list checks) — either drop the composition check or write down why the template deliberately deviates from ASVS here
- `backend/app/modules/auth/router.py`: rename the refresh cookie to use the `__Host-` prefix
- `GET /api/v1/auth/sessions` (list caller's active sessions) + `DELETE /api/v1/auth/sessions/{id}` (revoke one, scoped to the caller)
- `POST /api/v1/auth/oauth/{provider}/unlink` — self-service OAuth unlink
- New email template + send on: password change, 2FA enable/disable, OAuth link/unlink
- Frontend: password-strength meter + temporary-reveal toggle on password fields; confirm the access token is held in memory, not `localStorage`

**Tests**

- `backend/tests/test_auth_service.py` — change-password requires the correct current password, rejects on mismatch, revokes other sessions on success; security-notification email enqueued (mocked) on each of the three triggers
- `backend/tests/test_auth_router.py` — session list/revoke endpoints are scoped to the caller's own sessions (can't revoke someone else's)

**Done when**

- [ ] Authenticated password change works end-to-end, other sessions revoked
- [ ] `__Host-` cookie prefix live; session-list/revoke UI shipped in Settings
- [ ] ADR rows updated

---

### T-217 WebAuthn/FIDO2 second factor

> Filed by: T-213's ASVS checklist — closes rows V2.3.2, V2.9.1, V2.9.2, V2.9.3
> needs: T-024, T-213

**Goal** Offer a phishing-resistant second factor alongside TOTP, for users/orgs that want one.

**Build**

- `WebAuthnCredential` model (member ↔ credential public key, sign counter, AAGUID, nickname)
- A WebAuthn library (e.g. `py_webauthn`) drives registration (`navigator.credentials.create`) and assertion (`navigator.credentials.get`) ceremonies — `backend/app/modules/auth/webauthn.py`
- Router: `POST /auth/webauthn/register/options`, `POST /auth/webauthn/register`, `POST /auth/webauthn/login/options`, `POST /auth/webauthn/login`
- Sign-counter check on every assertion — reject and alert on a counter that goes backwards (clone-detection, same spirit as the existing refresh-token reuse detection in T-019)
- Treated as an alternative to TOTP at the same "2FA enabled" gate in `login()`, not an additional required factor

**Tests**

- Registration + assertion round-trip against a virtual authenticator
- Sign-counter regression rejected
- Existing TOTP flow unaffected when WebAuthn is unused

**Done when**

- [ ] A user can register a hardware key and log in with it instead of a TOTP code
- [ ] Counter-regression test passes
- [ ] ADR rows updated

---

### T-218 Step-up auth & mandatory admin 2FA

> Filed by: T-213's ASVS checklist — closes rows V2.5.7, V2.8.4, V2.8.5, V3.3.2, V3.7.1, V4.3.1, V4.3.3
> needs: T-024, T-140, T-213

**Goal** Sensitive actions and superadmin access require more than "has a valid 15-minute access token."

**Build**

- `require_superadmin` gains a check: `user.totp_enabled or user.webauthn_enabled` required — a superadmin with no second factor is rejected with an error telling them to enable one
- TOTP same-window replay tracking: store `(user_id, last_consumed_totp_step)`, reject a code for a step already consumed, log + notify on rejection
- Step-up re-auth dependency (`require_recent_auth(max_age_minutes=5)`) applied to: email change, 2FA disable (already effectively has this), billing plan changes, member removal
- Idle/periodic session policy: shorten the refresh token's max idle window or add a sliding expiry, per the ASVS L2 target (~12h or 30min idle)
- Documented admin-assisted recovery path for a user who's lost both their TOTP device and all backup codes (superadmin action, fully audited, same posture as impersonation in T-140)

**Tests**

- Superadmin login without 2FA enabled is rejected with an actionable error
- TOTP code reuse within the same 30s step is rejected and logged
- Step-up-gated routes 403 without a recent-enough auth, succeed after re-entering credentials
- Idle-timeout test using a frozen clock

**Done when**

- [ ] Superadmin accounts can't operate without 2FA enabled
- [ ] TOTP replay-within-window rejected
- [ ] Step-up gate covers the four listed actions
- [ ] ADR rows updated

---

### T-219 Data classification & PII handling

> Filed by: T-213's ASVS checklist — closes rows V1.8.1, V1.8.2, V6.1.1, V7.1.2, V7.1.3, V7.2.1, V7.2.2, V8.3.4, V8.3.8
> needs: T-213

**Goal** Replace "only the TOTP secret is classified/encrypted" with an actual, if modest, data-classification pass, and log the security events the ASVS pass found weren't being logged.

**Build**

- `docs/security/data-classification.md` — one row per PII-bearing column across all modules (`User.email`/`full_name`/`avatar_url`, `Membership`, GDPR `ConsentRecord`, etc.): classification (public/internal/sensitive), current protection, retention
- Encrypt `User.full_name` via the existing `EncryptedString` — the highest-value plaintext PII column; document the remaining plaintext columns as an accepted, reasoned scope decision rather than a silent gap
- Extend `_scrub()` in `backend/app/core/logging.py` to redact by known PII field *names* seen in practice (`email`, `full_name`), not just the existing secret-shaped key list
- Emit an audit/security-log event on permission-denied (403 from `require_permission`) and on a failed login attempt (not just the existing lockout counter increment)
- Apply a `GDPR_PURGE_GRACE_DAYS`-style retention/expiry to the next-highest-value data class named in the classification doc (expired sessions and GDPR-deleted users already have this)

**Tests**

- `backend/tests/test_logging.py` — a log record with `email`/`full_name` in `extra` gets redacted
- A 403 from `require_permission` produces an audit-log row
- `backend/tests/test_auth_service_login.py` — a failed login attempt produces a log/audit event, not just a counter increment

**Done when**

- [ ] Data-classification doc covers every PII column across every module
- [ ] `full_name` encrypted at rest
- [ ] Permission-denied and failed-login events logged
- [ ] ADR rows updated

---

### T-225 Build/infra hardening batch

> Filed by: T-213's ASVS checklist — closes rows V1.6.2, V1.9.1, V1.14.4 (shared with T-215), V2.4.5, V2.10.1, V2.10.3, V6.4.1, V6.4.2, V8.1.4, V8.1.5, V8.1.6, V9.1.2, V9.1.3, V9.2.2, V9.2.4, V12.1.3, V12.2.1, V12.3.4, V12.4.2, V13.1.5, V13.2.5, V14.2.5, V14.3.2, V14.3.3, V14.4.2, V14.5.1
> needs: T-213

**Goal** A grab-bag of smaller, mostly-independent infra/build hardening items the ASVS pass found. Grouped into one task because each is too small to justify its own task, not because they share an implementation.

**Build**

- SBOM: a `syft`/`cyclonedx-py` step in CI, uploaded as a workflow artifact
- Content-sniffing file-type validation on upload confirm (e.g. `python-magic` against the first N bytes, cross-checked against the declared `Content-Type`)
- File-count quota per org, alongside the existing byte quota
- `Content-Disposition: attachment` on API responses; explicit 406/415 rejection of unexpected `Content-Type`
- Confirm + document whether `/docs`/`/redoc` are disabled outside `dev`
- Confirm the `Server` header is stripped/replaced at the Caddy edge
- Confirm `sslmode=require` on `DATABASE_URL`/`REDIS_URL` outside `dev`
- Confirm OCSP stapling is active on Caddy's ACME certs
- TLS cipher-suite/version check added to the `zap-baseline`/`zap-full` CI jobs (or a dedicated `testssl.sh`/`sslyze` step)
- Documented and actually-tested backup/restore runbook for the managed Postgres instance
- Move `ENCRYPTION_KEY`/`JWT_PRIVATE_KEY`/etc. to a secrets vault/KMS where the hosting platform supports one, instead of plain env vars
- Add a secret pepper (vault-held) on top of Argon2's per-hash salt
- Reflective-File-Download-safe headers on file download responses
- Fail prod deploys where `VIRUS_SCANNER=noop`
- Request-anomaly alerting (Prometheus alert rule on abnormal per-user/per-IP request volume)
- Confirm/require TLS between the backend and Postgres/Redis within the Docker network
- Log/alert on requests using an HTTP method not valid for the matched route

**Tests**

- One test per mechanical check where feasible: content-sniffing rejects a mislabeled file, file-count quota enforced, SBOM artifact produced, `/docs` 404s outside dev, deploy fails fast on `VIRUS_SCANNER=noop` in prod config
- TLS/OCSP/cipher-suite items are infra-verification, not unit-testable — documented as a one-time manual verification + CI scan instead

**Done when**

- [ ] Each bullet above is either implemented or has a documented reason it's deferred further
- [ ] `docs/adr/0001-security-baseline.md` rows updated (`partial` → `met` for whichever land; the rest stay `partial` with an explicit note on what's still pending)

---

### T-226 Production-ready change-email flow

> Filed by: T-218, which needed step-up re-auth to gate *something* called "email change" but found no such feature built yet. Added a minimal request/confirm implementation as part of T-218 itself (`AuthService.request_email_change`/`confirm_email_change`, `EmailChangeToken`, `POST /auth/change-email` + `/auth/change-email/confirm`, gated by `require_recent_auth`) so the step-up gate had a real endpoint to protect, but deliberately scoped it down. This task is the "do it properly" follow-up.
> needs: T-218

**Goal** Bring the minimal T-218 implementation up to the bar the rest of the auth module holds (T-216/T-217's OAuth-unlink/WebAuthn-removal flows, password reset, etc.).

**Build**

- Re-verification of the *old* address, not just the new one: today `request_email_change` only proves control of the new address (confirm-link email) -- ASVS V2.5.5-adjacent flows generally also want the requester to prove they're the current account holder at request time (e.g. current password), beyond just having a valid session recent enough to pass `require_recent_auth`
- A grace/undo window: a `cancel_email_change` action reachable from the *old* address's `email_change_requested` notification, so a real owner who sees an unexpected request can stop it before the confirm link is clicked
- Rate limiting specific to repeated change-email requests targeting the same `new_email` (distinct from the existing per-IP/per-user `sensitive_limit`) -- prevents using the confirm-email flow to spam an arbitrary inbox
- Decide and document what happens to `email_verified_at`, active sessions, and any pending invitations tied to the old address when the change completes -- T-218's minimal version leaves all three untouched
- Frontend: a real settings-page card (T-218 shipped none) plus the `/confirm-email-change` confirmation route, following `ChangePasswordCard`'s pattern in `apps/web/src/features/settings/SecuritySettingsPage.tsx` and `VerifyEmailPage.tsx`'s route-confirmation pattern respectively
- Revisit whether `EmailChangeToken` should carry a device/IP fingerprint the way `Session` does, for the admin-assisted recovery / audit story

**Tests**

- Old-address re-verification is enforced (wrong current password rejected)
- Cancel-from-old-address invalidates the pending token
- Per-target-email rate limiting on repeated requests
- Frontend: settings card submits and shows pending/confirmed state; confirmation route handles valid/expired/already-used tokens

**Done when**

- [ ] Old-address proof required alongside new-address proof
- [ ] Cancel path shipped and tested
- [ ] Frontend card + confirmation route shipped
- [ ] ADR/task docs updated to point at this task instead of T-218 for the email-change feature itself

---

## P3-C · Comprehensive E2E + load

> HTML ref: §"Testing strategy" → "The test pyramid" (E2E + load rows), "Frontend & mobile testing", "Load & resilience"
>
> The money paths get browser/device-level coverage; everything else stays at unit/integration level. E2E suites are small and ruthless — a flaky E2E test gets fixed or deleted the week it flakes.

### T-220 Playwright E2E — signup → verify → invite → upgrade (Chromium + WebKit)

> HTML ref: §"Testing strategy" → "E2E web — Playwright"
> needs: T-044, T-056

**Goal** The Phase 1 acceptance flow — the one that must never break — runs as a browser test on every PR, on two engines.

**Build**

- `apps/web/e2e/` with `playwright.config.ts`: projects `chromium` + `webkit`, base URL `https://localhost` (dev Caddy cert trusted via `ignoreHTTPSErrors` in config), retries 1 in CI, trace on first retry
- Helpers:
  - `mailhog.ts` — poll Mailhog's API (`:8025/api/v2/search?kind=to&query=<email>`) for the verification / invitation email, extract the action link
  - `stripe.ts` — two upgrade strategies behind an env switch:
    - default (every PR): stub the payment by POSTing a signed `checkout.session.completed` test event to the T-030 webhook endpoint (reuse the webhook test fixtures) and asserting the UI reflects the paid plan
    - `STRIPE_E2E=1` (nightly): drive the real Stripe Checkout test-mode page with card `4242 4242 4242 4242`
- The spec, one continuous journey with unique per-run emails:
  1. Register → "check your inbox" screen
  2. Fetch verification email via Mailhog → visit link → logged-in dashboard
  3. Create org → invite `teammate+<run>@example.com`
  4. Second browser context: fetch invite email, accept, assert membership visible to both sides
  5. Upgrade to paid plan → assert plan badge + gated feature unlocked
- Selectors: `data-testid` only — add them to the app where missing; no text or CSS-path selectors
- CI job `e2e-web` (in the test workflow, needs the compose stack): boots stack, seeds, runs both projects, uploads traces on failure

**Tests**

- The suite is the test. Additionally: a deliberately broken invite flow on a throwaway branch fails the job (one-time gate verification)

**Done when**

- [ ] Full journey green on Chromium + WebKit in CI
- [ ] Zero non-`data-testid` selectors (lint rule or grep in CI)
- [ ] Failure artifacts (trace, screenshot) uploaded and usable

---

### T-221 axe-core accessibility gate in Playwright

> HTML ref: §"Testing strategy" → "Accessibility — axe-core"
> needs: T-220

**Goal** WCAG A/AA violations fail the build — accessibility is a test, not a hope.

**Build**

- `@axe-core/playwright` scan step appended to the E2E suite, run against each key page in its realistic state: login, register, dashboard, members list (with data), billing/plan page, settings, notification center (open), file upload
- Fail on any violation with impact `serious` or `critical`; `moderate` and below reported as annotations
- `apps/web/e2e/a11y-allowlist.ts`: rule/selector pairs that are explicitly accepted, each with a `// a11y-ok: <reason + date>` comment — empty allowlist is the goal
- Scan both color schemes if the app has a dark mode; include the RTL locale once T-231 lands (add a follow-up marker)

**Tests**

- Meta-verification once: add `aria-hidden="true"` to the login form on a throwaway branch → job fails

**Done when**

- [ ] All key pages scanned in CI, zero serious/critical violations
- [ ] Allowlist empty or every entry justified and dated

---

### T-222 Maestro mobile E2E — login, deep link, logout

> HTML ref: §"Testing strategy" → "E2E mobile — Maestro"
> needs: T-047

**Goal** The three mobile flows that break most often — auth, deep links, session teardown — verified on a real emulator.

**Build**

- `.maestro/` flows:
  - `login.yaml` — launch → login with seeded credentials → dashboard visible
  - `deep-link.yaml` — open a magic-link/invite deep link (`forge://…`) cold and warm → correct screen with correct state
  - `logout.yaml` — logout → secure store cleared (assert login screen on relaunch, no auto-login)
- Seeded test user dedicated to mobile E2E (`make seed` addition), pointing the app at the local backend via env
- CI: run on a macOS runner with the iOS Simulator (or Maestro Cloud if a device farm is preferred — write the choice down); nightly rather than per-PR (emulator boot cost), `workflow_dispatch` for on-demand
- Document local usage in `apps/mobile/README.md`: `maestro test .maestro/` against the dev stack

**Tests**

- The flows are the tests; each asserts on visible UI state, not timing sleeps (use Maestro's `assertVisible` with timeouts)

**Done when**

- [ ] All three flows green locally on iOS Simulator + Android emulator
- [ ] Nightly CI job green
- [ ] Cold-start deep link verified (app killed, link opens correct screen after auth)

---

### T-223 k6 load scripts with asserted budgets

> HTML ref: §"Testing strategy" → "Load & resilience"
> needs: T-048, T-056

**Goal** A latency regression fails a pipeline, not a launch. p95 < 300 ms and error rate < 1% are asserted, not eyeballed.

**Build**

- `tests/load/` k6 scripts modelling realistic traffic:
  - `login-burst.js` — spike of logins (rate-limit-aware: distinct virtual users, not one hammered account)
  - `browse.js` — authenticated list pagination: members, notifications, files (cursor-follows-cursor)
  - `presign.js` — file presign + confirm loop
  - `mixed.js` — weighted combination approximating production (80% reads / 15% writes / 5% auth), ramping 0 → 50 VUs → hold 3 min
- Shared thresholds in every script:
  ```javascript
  thresholds: {
    http_req_duration: ["p(95)<300"],
    http_req_failed:   ["rate<0.01"],
  }
  ```
- Data setup: `make seed-load` creates orgs with realistic volumes (1k members, 10k notifications) so pagination is actually exercised
- `make load-test` runs the suite against the local compose stack; CI runs it **nightly** against a dedicated compose stack on a consistent runner class (per-PR runs on shared runners produce noise, not signal — say so in the README)
- Results exported as a JSON summary artifact; a trend note is appended to `tests/load/RESULTS.md` per run

**Tests**

- Thresholds present in every script (CI grep); nightly run green against seeded stack

**Done when**

- [ ] All four scripts pass budgets against the seeded local stack
- [ ] Nightly CI job wired with artifacts
- [ ] README explains why load gates are nightly, not per-PR

---

### T-224 Visual regression — Playwright snapshots on dashboard + settings

> HTML ref: §"Testing strategy" → "Visual regression — Playwright snapshots"
> needs: T-220

**Goal** Unintended UI changes on the two most-seen pages are caught by pixel comparison, with a sane update workflow so the suite doesn't rot.

**Build**

- `toHaveScreenshot()` specs for: dashboard (seeded, deterministic data), settings (profile + org tabs), and the app shell at mobile viewport (375px) + desktop (1280px)
- Determinism rules:
  - dynamic regions masked (`mask:` option): avatars, timestamps, notification badges
  - animations disabled (`reducedMotion: "reduce"` + CSS override), fonts loaded before capture
  - fixed seed data — snapshots never depend on run order
- Baselines committed per browser/platform under `apps/web/e2e/__screenshots__/`; CI compares with `maxDiffPixelRatio: 0.01`
- Update workflow documented in the spec file header: `pnpm e2e --update-snapshots` locally, reviewer eyeballs the diff in the PR — baseline changes must be intentional and visible in review

**Tests**

- Suite green twice in a row on CI (proves determinism, not just correctness)

**Done when**

- [ ] Snapshots stable across two consecutive CI runs
- [ ] A deliberate 2px layout change fails the job (verified once)
- [ ] Update workflow documented

---

## P3-D · i18n expansion + advanced

> HTML ref: §"Build phases" → "Phase 3" (i18n row)
>
> Foundation already in place from T-067–T-070 (`en` + `fi`, server-side locale resolution, localised emails). This section adds languages, RTL, translator tooling, and locale-aware routing. §0.11 remains binding: any hardcoded string found during this work is a defect to fix, not to note.

### T-230 Three more locales — de, es, fr

> needs: T-067

**Goal** The template ships with six locales (`en`, `fi`, `de`, `es`, `fr` + the T-231 RTL locale), and CI guarantees no locale silently rots.

**Build**

- Full translation of every `@forge/i18n` key into `de`, `es`, `fr` under `packages/i18n/locales/<locale>/` — namespace files mirror `en/` exactly
- Machine-translate first pass is acceptable **if** flagged: untranslated-quality keys carry a `"__review": true` sibling marker file listing keys needing human review; the goal state is zero markers
- `packages/i18n/scripts/check-locales.ts`:
  - For every non-`en` locale: fail if >5% of `en` keys are missing
  - Fail on **orphan keys** (present in a locale but absent from `en`)
  - Fail if any `en` value is empty
  - Wired into `pnpm lint` and CI
- Locale metadata registry in `packages/i18n/src/locales.ts`: `{ code, nativeName, dir }` — the language switcher (T-069) and mobile picker (T-070) render from this, no hardcoded lists
- Pluralisation spot-check: keys using count-based plurals verified for each new language's plural rules (German/French/Spanish differ from English in zero/one handling)

**Tests**

- `check-locales` passes for all locales; deliberately deleting 6% of `de` keys fails it
- Orphan-key detection: adding a `de`-only key fails
- Language switcher shows all six locales from the registry; switching to `de` renders a known translated string

**Done when**

- [ ] All locales ≥95% coverage, CI-enforced
- [ ] Zero orphan keys
- [ ] Switcher + mobile picker driven by the registry

---

### T-231 RTL support — add `ar`, verify layout flips

> needs: T-069, T-230

**Goal** One right-to-left locale (`ar`) works correctly across web and `packages/ui` — proving the layout system is direction-safe, not just translated.

**Build**

- `ar` locale added per T-230 conventions (`dir: "rtl"` in the locale registry)
- Web: `<html dir>` set reactively from the active locale's `dir` (i18next `languageChanged` listener); persisted across reloads
- `packages/ui` sweep: replace physical direction utilities with logical ones — `ml-*/mr-*` → `ms-*/me-*`, `pl-*/pr-*` → `ps-*/pe-*`, `left-*/right-*` → `start-*/end-*` where the intent is reading-direction-relative (deliberate physical positioning, e.g. a close button pinned top-right in both directions, gets a `/* rtl-ok: <reason> */` comment)
- ESLint/grep CI rule: physical direction classes in `packages/ui` and `apps/web` require the `rtl-ok` annotation
- Directional icons (chevrons, arrows, "back") flip via a `useDirection()` hook from `packages/hooks`
- Mobile: document `I18nManager` behaviour (RTL on native requires app restart); enable and verify the auth + settings screens; full mobile RTL polish may be deferred with a written note, web may not

**Tests**

- Switching to `ar` sets `dir="rtl"` on `<html>` (Playwright assertion)
- Visual snapshot (T-224 mechanism) of dashboard + settings in `ar` — sidebar, nav, and text alignment mirrored
- CI grep: zero unannotated physical direction classes in `packages/ui`
- axe-core pass (T-221) on the `ar` locale

**Done when**

- [ ] Dashboard + settings render correctly mirrored in `ar`
- [ ] Logical-properties rule enforced in CI
- [ ] RTL snapshots committed as baselines

---

### T-232 Translator workflow — export, import, source-of-truth checks

> needs: T-067, T-230

**Goal** A non-developer can translate the product without touching git, and the developer loop for getting those translations back in is one command.

**Build**

- `pnpm i18n:export [--locale de] [--missing-only]` → writes `i18n-export/<locale>.csv` (columns: key, en value, current translation, context/namespace) — CSV because every tool and every human can open it; also emit gettext `.po` for tool compatibility
- `pnpm i18n:import i18n-export/de.csv` → merges back into the locale JSONs, preserving key order, failing on unknown keys
- TMS integration: document (not implement) the Crowdin and Lokalise setups in `docs/runbooks/translations.md` — config file examples, sync direction (repo is the source of truth for `en`, TMS is upstream for translations), and the manual CSV loop as the zero-dependency fallback
- Source-of-truth CI checks (extends T-230's `check-locales`):
  - `en` is the **only** locale required at 100% — a key added to any other locale first is an orphan (already failing per T-230)
  - export/import round-trip is lossless: `export → import` produces zero diff (CI job on the `en` locale)

**Tests**

- Round-trip test: export `de`, re-import unchanged, `git diff --exit-code` clean
- Import with an unknown key → exits non-zero with the offending key named
- `--missing-only` export contains exactly the keys absent from the target locale

**Done when**

- [ ] Export + import commands work and are documented
- [ ] Round-trip CI check green
- [ ] `docs/runbooks/translations.md` covers TMS and manual workflows

---

### T-233 Locale-aware URL routing on web

> needs: T-069, T-042

**Goal** The locale lives in the URL (`/en/settings`, `/fi/settings`) so pages are shareable, bookmarkable, and indexable per language — and T-305 (marketing SEO) can rely on the same convention.

**Build**

- TanStack Router: top-level `/$locale` layout route wrapping the app route tree; `$locale` validated against the T-230 registry (unknown → 404)
- Redirect rules:
  - `/` and any locale-less deep link → resolved locale prefix (resolution order mirrors the backend: user preference → `Accept-Language` → `en`), via a redirect route, HTTP-level where SSR/edge applies
  - Language switcher (T-069) rewrites the current path's locale segment in place — never loses the page the user is on
- The active URL locale wins over the stored preference for rendering (a shared `/de/...` link shows German even to an `en` user), but does not silently overwrite `User.locale` — switching explicitly does
- `<link rel="canonical">` + `hreflang` alternates emitted per page via a small head component (required by T-305; app pages behind auth get canonical only, `noindex` stays on)
- Auth redirects, invite links, and email deep links updated to carry the locale segment; legacy locale-less links keep working via the redirect rule (backwards compatibility test)

**Tests**

- `/settings` visited by a `fi` user → redirected to `/fi/settings`
- `/de/settings` renders German regardless of stored preference; explicit switch updates `User.locale`
- Unknown locale `/xx/settings` → 404
- Old bookmarked locale-less URL → 301/redirect to prefixed equivalent
- Switcher preserves path + query across locale change

**Done when**

- [x] All routes locale-prefixed; redirects cover legacy links
- [x] hreflang/canonical component emits correct alternates
- [x] E2E (T-220) still green with prefixed URLs

---

### T-234 Locale-aware push notifications — verified end-to-end

> needs: T-068, T-105

**Goal** Prove (don't assume) that a push notification reaches each recipient in their own language — the full chain from event to Expo payload.

**Build**

- Audit the chain built in T-068/T-102/T-105: event → `NotificationService` fan-out → per-recipient locale lookup (`User.locale`) → translated title/body via backend `t()` → Expo payload. Fix any point where the sender's locale, `request.state.locale`, or a default leaks into another recipient's message (the classic bug: translating once before the fan-out loop instead of per recipient)
- Localise at **send time** per recipient, matching the email rule in §0.11
- Push payload carries `"locale"` in its data dict so the mobile client can localise any client-side rendering consistently
- Integration test fixture: capture outbound Expo API calls (mock transport) and assert on payload content

**Tests**

- Org with user A (`locale="en"`) and user B (`locale="fi"`); trigger one `payment_failed` event notifying both → A's push title is the English string, B's is the Finnish string, from the same event
- Recipient with an unsupported stored locale falls back to English (never raw key strings)
- Recipient locale changes between event creation and job execution → job uses the locale at send time
- In-app + email channels for the same event are also per-recipient localised (regression net around T-102)

**Done when**

- [x] Mixed-locale fan-out test passes
- [x] Fallback + late-change tests pass
- [x] Any bug found in the audit is fixed with its own regression test

---

## P3-E · Performance tuning

> HTML ref: §"Build phases" → "Phase 3" (performance row) | §"Best practices" → "Performance"
>
> §0.9 already mandates async everywhere, ARQ for slow work, and Redis on hot reads. This section verifies those claims with tests and budgets, and adds the infrastructure pieces (PgBouncer, key rotation at scale) that only matter under load.

### T-240 Redis cache layer — invalidation proven by tests

> needs: T-021

**Goal** The three hot reads (current user, org settings, plan features) are cached with 60 s TTL and — the part that actually breaks in production — invalidated correctly on every write path.

**Build**

- `get_current_user` cache exists from T-021 (`core/dependencies.py`, with `invalidate_user_cache`). This task completes the set:
  - `backend/app/core/cache.py`: small typed helper — `cached_json(key, ttl=60)` get/set + `invalidate(key)`; all cache keys defined in one module (`cache_keys.py`-style constants), never inline f-strings at call sites
  - Org settings cache (`org:{org_id}:settings`) read in `get_current_org`; plan features cache (`org:{org_id}:plan`) read by the billing/feature-gate path
- Invalidation matrix — every write that must bust each key, enumerated and wired:
  - user cache: profile update, locale change, deactivation, role/permission change, `is_superadmin` flip, GDPR delete
  - org settings: org update, org deletion
  - plan features: subscription change (checkout completed, plan override T-141, dunning downgrade), feature-flag toggle (T-122)
- Serialization: cached payloads are versioned (`{"v": 1, ...}`) — a deserialization mismatch is a cache miss, never an exception
- Redis unavailable → cache layer degrades to direct DB reads (log a warning, don't 500) — consistent with T-206's philosophy

**Tests**

- Query-count proof (uses T-243's fixture): two consecutive `get_current_user` calls → second issues zero DB queries
- Each row of the invalidation matrix: write → immediately read → fresh value (parametrised test over the matrix)
- Deactivated user is rejected on the next request, not after TTL expiry (security-relevant invalidation)
- Versioned-payload mismatch → treated as miss
- Redis down (mock) → reads still succeed from DB

**Done when**

- [x] All three caches live with the invalidation matrix fully tested
- [x] Deactivation propagates immediately
- [x] No inline cache-key strings (grep check)

---

### T-241 TanStack Virtual on member list + notification list

> needs: T-044, T-104

**Goal** The two lists that grow unboundedly render a constant number of DOM nodes regardless of data size.

**Build**

- `@tanstack/react-virtual` (confirmed-clean family per §0.12 — still pin latest) on:
  - members list (T-044 settings/members page)
  - notification center list (T-104)
- Integration with the existing cursor-based `useInfiniteQuery`: virtualizer range end nearing the loaded count triggers `fetchNextPage` — scroll position is the pagination driver
- Preserve semantics: row height measurement for variable-height notification items, `role="list"` / `role="listitem"` on the scroll container contents, keyboard navigation (arrow keys move focus through virtual rows without scroll jank)
- Scroll restoration on back-navigation (TanStack Router scroll restoration + virtualizer `initialOffset`)

**Tests**

- Render with 1,000 seeded items → DOM contains < 50 row nodes (RTL assertion on the container)
- Scrolling to the bottom of loaded data triggers exactly one next-page fetch (MSW)
- Keyboard: focus moves through rows across a virtualization boundary
- axe-core (T-221) still passes on both pages with virtualization active

**Done when**

- [x] Both lists virtualized, DOM-node budget asserted in tests
- [x] Infinite scroll + a11y + scroll restoration verified — infinite scroll,
      keyboard-across-a-boundary, and axe-core are asserted in vitest against
      the real virtualizer (`apps/web/src/test/virtualLayout.ts` supplies the
      layout jsdom can't); scroll restoration is wired end to end but only
      observable in a real browser
- [ ] No regression in the T-224 snapshots — not re-run here (needs the Docker
      stack + Linux-generated baselines). The three snapshot targets
      (dashboard, settings-profile, settings-org) don't include the member
      list, and the notification panel is closed in all of them, so no
      baseline change is expected

---

### T-242 Per-route bundle budgets in CI

> needs: T-040

**Goal** Bundle growth is a reviewed decision, not an accident discovered in Lighthouse six months later.

**Build**

- `size-limit` (with the file preset) configured in `apps/web/package.json` against the built output:
  - initial JS for the entry route (login) — budget ~150 KB gzipped
  - each lazy route chunk (dashboard, settings, billing, notifications, admin) — per-chunk budgets set from current size + 10% headroom
  - total CSS budget
- Budgets are explicit numbers committed in config — raising one requires editing the file in the PR, which is the point
- `rollup-plugin-visualizer` wired into the build (`pnpm build --analyze`) emitting `stats.html`; CI uploads it as an artifact on every build so regressions are diagnosable without reproducing locally
- CI job `bundle-budget` in the frontend workflow: build → `size-limit` → fail on any exceeded budget, printing the per-chunk delta table
- `docs/runbooks/bundle-budget.md`: what to do when the gate fails (analyze artifact → lazy-load / replace dep / consciously raise budget with reviewer sign-off)

**Tests**

- CI job green at current sizes; deliberately importing a large lib eagerly on a throwaway branch fails it (one-time verification)

**Done when**

- [ ] Budgets committed for entry + every lazy chunk
- [ ] `stats.html` artifact on every CI build
- [ ] Gate failure verified once, runbook written

---

### T-243 pytest query-count fixture — N+1 detection on every list endpoint

> needs: T-048

**Goal** An N+1 query introduced on any list endpoint fails a test with a diff of the executed SQL — the day it's introduced, not when the table grows.

**Build**

- `backend/tests/fixtures/query_counter.py`:

  ```python
  @asynccontextmanager
  async def assert_max_queries(session, n: int):
      """Counts statements via SQLAlchemy's before_cursor_execute event
      on the test connection. On failure, prints every captured statement
      so the N+1 is visible in the diff, not just a number."""
  ```

- Parametrised test module `tests/test_query_budgets.py` covering every list endpoint (members, invitations, notifications, files, invoices T-133, audit log, admin directory T-141):
  - Seed 3 items → record query count; seed 30 items → assert count is **identical** (constant-query property — stronger and less brittle than a magic number)
  - Budget registry: `{endpoint: max_queries}` dict in the test module; a new list endpoint without an entry fails a meta-check (mirrors the T-049 sweep pattern)
- Fix any N+1 found while wiring this: the usual suspects are relationship access after list load — fix with `selectinload` per §0.3 (never `joinedload` for collections)

**Tests**

- The suite is the deliverable; plus: meta-check fails when a new `list_`-style route lacks a budget entry (verified by adding a dummy route in the test)

**Done when**

- [x] Constant-query property holds on every list endpoint — 15 endpoints,
      each measured at 3 rows and again at 30. `GET /orgs/{org_id}/invitations`
      (MEMBERS_MANAGE, cursor-paginated) was built here: the brief named an
      invitations list, but the tenancy module only created, previewed and
      accepted them
- [x] Every N+1 found is fixed and stays covered — three in the admin module
      (audit feed, user detail, dashboard payment failures); all three were
      bare-UUID lookups with no ORM relationship to `selectinload`, so each is
      a batched `IN` via the new `list_by_ids` repository methods
- [x] Meta-check guards future endpoints

---

### T-244 PgBouncer in transaction mode (prod)

> needs: T-005

**Goal** Connection count to Postgres stays flat as backend replicas scale — without breaking asyncpg.

**Build**

- `docker-compose.prod.yml`: `pgbouncer` service (bitnami/pgbouncer or edoburu image — pick and pin):
  - `pool_mode=transaction`; `default_pool_size` sized to `max_connections * 0.8` minus a superuser reserve; auth via userlist file from env
  - healthcheck (`psql -c 'SHOW POOLS'` or TCP), backend `depends_on` pgbouncer healthy
  - backend `DATABASE_URL` points at pgbouncer; **Alembic migrations connect directly to Postgres** (session-level locks + DDL don't mix with transaction pooling) — separate `MIGRATIONS_DATABASE_URL` setting, defaulting to `DATABASE_URL` in dev
- The asyncpg tradeoff, handled not hand-waved: transaction pooling breaks server-side prepared statement reuse across connections →
  - `create_async_engine(..., connect_args={"statement_cache_size": 0})` when `PGBOUNCER_ENABLED=true` (new setting)
  - documented in `docs/adr/0002-pgbouncer.md`: why transaction mode, the prepared-statement tradeoff, measured overhead, and the alternative (server-side cache in newer asyncpg) if it becomes a bottleneck
- Dev compose stays direct-to-Postgres — one less moving part locally

**Tests**

- Prod-compose integration (CI service matrix or dedicated job): app boots through pgbouncer, `/health/ready` 200, a request that opens two sequential transactions succeeds
- With `statement_cache_size=0`: repeated identical queries succeed (no `DuplicatePreparedStatementError`)
- Migration path: `alembic upgrade head` via `MIGRATIONS_DATABASE_URL` succeeds while app traffic flows through pgbouncer

**Done when**

- [x] Prod stack runs all traffic through pgbouncer, migrations direct —
      `edoburu/pgbouncer:v1.25.2-p0`, `pool_mode=transaction`, and the new
      `pgbouncer-prod-compose` CI job boots the real prod compose stack and
      migrates over `MIGRATIONS_DATABASE_URL` while pooled traffic flows.
      Two pre-existing defects had to be fixed for that stack to boot at all:
      its postgres service had no `POSTGRES_PASSWORD` (now `.env.postgres`),
      and both Python services' build context couldn't satisfy
      `backend/Dockerfile`'s `COPY`s (now the repo root, as in CI)
- [x] Prepared-statement regression test passes — `tests/test_pgbouncer.py`
      runs a real pgbouncer in front of a real Postgres and asserts *both*
      directions: the unconfigured engine deterministically fails with
      `InvalidSQLStatementNameError`, the configured one doesn't. Three knobs
      were needed, not the one the brief named: `statement_cache_size=0` plus
      `prepared_statement_cache_size=0` (SQLAlchemy's own per-connection LRU,
      which is the layer that actually fires) plus UUID statement names
- [x] ADR documents the tradeoff with numbers — `docs/adr/0002-pgbouncer.md`:
      +0.89ms median for the proxy hop, +0.65ms median for the disabled
      statement cache, 20 app connections resolving to 4 Postgres backends,
      and ~115 leaked prepared statements per server connection without
      `server_reset_query_always`

---

### T-245 Production-ready encryption key rotation job

> needs: T-009.5, T-034

**Goal** Rotating `ENCRYPTION_KEY` on a database with millions of encrypted rows is a routine, observable, resumable operation — upgrade T-009.5's stub to the real thing.

**Build**

- `backend/app/workers/jobs/encryption.py`, rewritten:
  - **Column discovery**: walk SQLModel metadata for every column whose type is `EncryptedString` — no hardcoded table list; new encrypted columns are picked up automatically
  - **Batched cursor scan** per (table, column): keyset pagination on PK (no OFFSET, §0.3), `batch_size` configurable (default 500), per-batch commit
  - **Checkpointing**: progress (`table`, `column`, `last_pk`, counts) persisted via `JobRecord` (T-034) after every batch — a crashed or redeployed worker resumes from the checkpoint, and re-running is idempotent (already-current ciphertext is skipped by token-prefix check)
  - **Dry-run mode**: counts rows needing re-encryption per column, writes nothing, prints the work estimate
  - **Metrics** (T-202): `forge_reencrypt_rows_total{table,column}`, `forge_reencrypt_remaining` gauge, batch duration histogram
  - **Throttle**: configurable inter-batch sleep so the job never saturates the pool (§0.9: `max=20`)
- `docs/runbooks/encryption-key-rotation.md`, the operation end-to-end:
  1. Generate new key → set as `ENCRYPTION_KEY`, demote old key to `ENCRYPTION_KEY_OLD` → deploy (MultiFernet reads both)
  2. Dry-run → review counts
  3. Run job → watch the `remaining` gauge → handle a mid-run crash (resume) and a mid-run rollback (keys unchanged, safe)
  4. Verify zero rows remain on old key → remove `ENCRYPTION_KEY_OLD` → deploy
  5. Rollback story at every step

**Tests**

- Discovery finds all `EncryptedString` columns (assert against known models; add a throwaway model → it's discovered)
- Rotation: 1,500 rows under key A → run with key B primary → all decrypt under B alone; job report matches
- Resume: kill after batch 1 (simulated) → re-run → completes without re-processing batch 1 (checkpoint assert)
- Dry-run writes nothing (query-count/mutation assert)
- Mixed state mid-rotation: reads of not-yet-rotated rows still succeed via MultiFernet

**Done when**

- [x] Stub replaced; discovery, checkpointing, dry-run, metrics all tested —
      `reencrypt_column_job(table=, column=)` is gone, replaced by
      `rotate_encryption_keys_job` (+ a `python -m app.workers.jobs.encryption`
      CLI, since `WorkerSettings.job_timeout` is 300s and a real rotation
      isn't a five-minute job). Discovery walks `SQLModel.metadata` and
      refuses, with a named error, a composite PK or an encrypted PK — neither
      can be keyset-paged. The scan goes through a Text-typed mirror `Table`
      rather than `text()` with interpolated identifiers, so the job moves raw
      tokens with SQLAlchemy still doing the quoting
- [x] Runbook walks the full rotation including crash recovery —
      `docs/runbooks/encryption-key-rotation.md`, five steps with expected
      output and a rollback each. The step that mattered most to write down:
      the verification scan *before* dropping `ENCRYPTION_KEY_OLD` must report
      `rotated_total: 0`, because a row still on the old key when that key
      goes away gets its ciphertext re-encrypted as if it were plaintext
- [x] 100% coverage on the job module — 248 statements, 44 branches, 18 tests
      against a real Postgres + Redis. Two things the spec didn't call out but
      the build needed: the `forge_reencrypt_remaining` gauge is published
      through Redis (T-202's `forge_active_sessions` split — the job's process
      doesn't serve `/metrics`), and `[tool.coverage.report] exclude_also`
      now drops `if __name__ == "__main__":`, which otherwise makes a module
      with a CLI uncoverable by construction

---

### T-246 Read-replica plumbing (optional — scale-triggered)

> needs: T-005

**Goal** The code path for routing reads to a replica exists and is tested against a single database — so enabling a real replica later is an infra change, not a refactor. **Do not provision an actual replica now.**

**Build**

- `backend/app/core/db.py`: `write_engine` (existing) + `read_engine` — when `READ_REPLICA_URL` (new optional setting) is unset, both names bind to the same engine; zero behaviour change by default
- Session routing: `get_session(read_only: bool = False)` — explicit opt-in per repository method via a `use_read_replica=True` argument on safe read methods (`list_cursor`, `get` on non-auth paths); **never** implicit by HTTP verb
- Hard rules, documented in the code and the ADR:
  - auth reads (sessions, tokens, permissions) always hit the primary — replication lag on auth is a security bug
  - read-your-writes flows (create → immediately list) stay on the primary within the request
- `docs/adr/0003-read-replica.md`: replication-lag consistency implications, which query classes are lag-tolerant, and the activation trigger (sustained primary CPU > 60% from read traffic, or pool exhaustion from reads — with dashboard links from T-204/T-205), plus the activation checklist

**Tests**

- `READ_REPLICA_URL` unset → both engines are the same object; all existing tests pass unchanged
- With a second testcontainers Postgres as a fake replica: `use_read_replica=True` queries hit it (assert via connection inspection); auth repository queries hit the primary regardless
- Flag on a mutating method → raises at definition time (misuse guard)

**Done when**

- [x] Plumbing merged, default-off, zero behaviour change (full suite green with flag unset)
      — `READ_REPLICA_URL` unset makes `read_engine` *the same object* as
      `write_engine` (and `read_session_factory` the same as
      `async_session_factory`), so a flagged read resolves to the caller's own
      session: no second pool, no branch. Full backend suite green (1735
      passed, 6 skipped)
- [x] Routing + auth-pinning tests pass — `tests/test_read_replica.py` runs a
      second testcontainers Postgres as a stand-in replica (deliberately *not*
      replicating: rows seeded on one side only make "which database answered"
      decidable) and asserts routing at the connection level via
      `before_cursor_execute`. Auth pinning is enforced twice: auth
      repositories expose no `use_read_replica` at all, and
      `db.PRIMARY_ONLY_TABLES` pins per *statement*, so a join or subquery
      that reaches auth state is pinned too
- [x] ADR captures when to actually enable this — `docs/adr/0003-read-replica.md`:
      both triggers point at named panels (`forge-database`'s "Pool
      utilization" and "hall of shame", `api-overview`'s in-flight requests),
      the cheaper fixes to rule out first, a per-query-class eligibility
      table, and a 7-step activation checklist (replica pool gauge +
      replication-lag alert land *before* the second call site)
- Two things the brief's shape had to change to survive contact: `get_session`'s
      `read_only` is annotated as a FastAPI dependency rather than a bare
      `bool = False`, because a bare default publishes `?read_only=` as a query
      parameter on all ~250 routes that depend on it — i.e. it would make read
      routing client-settable, the one thing this design forbids (asserted
      against the real OpenAPI schema). And the misuse guard rejects the flag on
      any method not *named* like a read (`create`, `get_or_create` and an
      ambiguous `sync_everything` all raise `TypeError` at class-definition
      time), rather than trying to detect mutation itself

---

## P3-F · Module scaffold

> HTML ref: §"Extending the template" (entire section)
>
> The measure of the template is how fast "new idea" becomes "first endpoint". This section makes the module pattern copyable in one command and gives decisions and operations their standing document types.

### T-250 `_template` module — all five files, working example

> needs: T-009, T-021

**Goal** `backend/app/modules/_template/` (currently an empty `.gitkeep`) becomes a complete, compilable reference module demonstrating every §0 law in ~200 lines.

**Build**

- The five files per §2 module anatomy, using `__MODULE_NAME__` / `__ModelName__` placeholders where the generator will substitute, around one example org-scoped entity `Item`:
  - `models.py` — `Item` with `UUIDPKMixin + TimestampMixin + SoftDeleteMixin + OrgScopedMixin`, one indexed filter column
  - `schemas.py` — separate `ItemCreateRequest` / `ItemUpdateRequest` / `ItemPublic` (§0.4: no mass assignment)
  - `repository.py` — extends `BaseRepository[Item]`; one custom query method showing the org-scope + soft-delete pattern
  - `service.py` — create/list/update/delete raising typed `DomainError`s; one plan-limit check example (`PlanLimitError`)
  - `router.py` — CRUD under `/api/v1/__MODULE_NAME__/`, envelope responses, `require_permission` gates, cursor pagination, `api_limit`
  - `README.md` — public interface, events emitted, and a checklist of what to customise after generating
- Matching test template `backend/tests/modules/_template_test.py.tmpl`: cross-tenant isolation test, RBAC gate test, cursor pagination test — the non-negotiables from §0.8 pre-written
- Exclusions verified, not assumed: `_template` is skipped by the module registry/loader (T-060 — confirm the underscore convention is enforced there), by router registration (T-061), and by Alembic autogenerate (T-062) — add explicit guards if any relies on convention alone
- The template code itself passes `ruff` + `mypy --strict` (placeholders chosen to be valid identifiers)

**Tests**

- `ruff check` + `mypy --strict` pass on `_template/` as-is
- App boots with the template present: no `_template` routes in the OpenAPI schema, no `item` table in autogenerate output
- Registry test: `_template` absent from loaded modules

**Done when**

- [x] All five files + README + test template exist and are lint/type-clean —
      `models.py` / `schemas.py` / `repository.py` / `service.py` /
      `router.py` + `README.md` around one org-scoped `Item`, plus
      `tests/modules/_template_test.py.tmpl`. `ruff check`, `ruff format
      --check` and `mypy --strict` all pass on `_template/` exactly as it
      sits, which is only possible because the module imports itself
      *relatively* (`from .models import Item`): an absolute
      `app.modules.__MODULE_NAME__.models` is unresolvable until the module
      is generated, so mypy could never check the template itself — and the
      relative form survives the rename for free. `.gitkeep` is gone; the
      directory has real contents now
- [x] Exclusion from registry, routing, and migrations proven by tests —
      `tests/test_module_template.py`, plus
      `test_autogenerate_never_proposes_the_template_scaffolds_table` in
      `test_conditional_migrations.py`. Registry and routing turned out to
      exclude the scaffold *by construction* rather than by the underscore
      convention (`ModuleName` is a closed `Literal`; `build_router()` and
      `app/models/__init__.py` are hand-written lists), so those tests assert
      the property and nothing had to change. Alembic was the one place
      relying on nothing at all: `target_metadata` **is** the process-wide
      `SQLModel.metadata`, so a single import of the scaffold anywhere in the
      process registers `Item` in front of autogenerate — and
      `tests/test_read_replica.py` already does exactly that, sweeping every
      `app.modules.*.repository` through `pkgutil`. `alembic/env.py` now
      drops any object whose (parent) table name still carries the
      `__MODULE_NAME__` placeholder: exact, not heuristic, because generation
      substitutes every occurrence away (T-251). Verified by deleting the
      guard and watching the test fail
- [x] Example demonstrates envelope, RBAC, cursor pagination, org-scoping, and typed errors —
      five CRUD routes, each with `@api_limit`, `require_permission(...)` and
      an `Envelope`/`Meta` response; `list_items` pages by cursor with the
      next one in `meta.next_cursor`; `ItemRepository.count_active` is the
      worked example of the org-scope + soft-delete pattern a hand-written
      query has to repeat; `NotFoundError` / `UnprocessableEntityError` /
      `PlanLimitError` all leave the service typed
- Four decisions the brief's shape didn't settle. (1) `require_permission`
      alone gates every route and yields the `Membership`, so `org_id` and
      the actor's `user_id` come from the gate rather than from the client —
      but a `<module>:read` / `<module>:manage` pair cannot exist before the
      module does, so `router.py` starts from two named constants pointing at
      existing permissions, and repointing them is step 2 of the README's
      checklist. (2) The plan-limit example is a module constant, not a
      `require_feature` entitlement: a gate that denies would make a
      brand-new module 402 until someone configured a plan, and coupling the
      scaffold to billing would break it for products that disable billing
      (T-065) — the README points at `storage.service._check_quota` for the
      real thing. (3) The table is `__MODULE_NAME___item`, not `item`, so two
      modules generated before either entity is renamed don't collide on
      `SQLModel.metadata` at import time. (4) The test template drives the
      module through a small FastAPI app it assembles itself, overriding only
      `get_current_org` — that is what lets the generated tests be green
      *before* the router is registered and before the migration exists
      (README steps 3 and 4), and it is what lets one test choose the calling
      org and another the calling role. `[tool.coverage.run] omit` now
      excludes `_template` — it ships uncalled by design, and measuring it
      would charge every future template line against CI's
      `--cov-fail-under=85`

---

### T-251 `make new-module NAME=foo` — generation, not just copying

> needs: T-250

**Goal** One command turns the template into a registered, tested, compiling module — the existing naive `cp -r` target grows the renaming and guidance that make it actually usable.

**Build**

- Upgrade the `new-module` Makefile target (delegating to `backend/scripts/new_module.py` — logic in Python, not shell):
  1. Validate `NAME` (snake_case, not a reserved/existing module name; clear error otherwise)
  2. Copy `_template/` → `modules/<name>/`, substituting `__MODULE_NAME__` → `<name>`, `__ModelName__` → `<Name>` in file contents
  3. Copy the test template → `tests/modules/test_<name>.py` with the same substitution
  4. Print the manual follow-ups it deliberately does **not** do (each with file:line pointers): register in the module registry (T-060), add to `product.config` toggles (T-059), run `alembic revision --autogenerate -m "<name>_tables"`, rename `Item` to the real entity
- Generated module must pass `ruff` + `mypy --strict` and its generated tests immediately (against the generic `Item` entity) — a broken starting point poisons every future module
- `docs/runbooks/new-module.md`: the full loop from `make new-module` to first deployed endpoint, including the §"Extending the template" workflow from the HTML plan

**Tests**

- CI smoke: `make new-module NAME=zztest` → generated module passes `ruff`, `mypy --strict`, and its own tests; then cleaned up (temp workspace)
- `make new-module` without `NAME`, with `NAME=Foo-Bar`, with `NAME=auth` → each fails with a usable message
- Substitution completeness: no literal `__MODULE_NAME__` remains anywhere in the generated output (grep)

**Done when**

- [x] Generated module is green (lint + types + tests) with zero manual edits —
      `make new-module NAME=zztest` → `ruff check`, `ruff format --check`,
      `mypy --strict` and its own 5 tests all pass untouched, verified end to
      end here. The generated tests are green *before* the router is
      registered and before the migration exists (T-250's test template
      assembles its own FastAPI app), so "green with zero edits" is a real
      starting point rather than a definition. `ruff format` runs over the
      output as a guard: the placeholder is 15 characters wide, so
      substitution moves every line carrying it, and
      `test_generation_changes_nothing_that_ruff_would_change` pins the fact
      that today the formatter never actually has to rewrite anything — the
      day a template edit makes it a repair, that test says so
- [x] Follow-up checklist printed with accurate pointers — eight steps
      mirroring the module README's, each `file:line` resolved against the
      real tree at generation time by searching for an anchor rather than
      being typed, so they cannot rot;
      `test_every_shared_pointer_resolves_in_the_real_tree` fails if an
      anchor is ever renamed away. Writing the pointers turned up one the
      README was missing: `alembic/env.py`'s `load_models_for_migration()` is
      a hand-written import list, so `alembic revision --autogenerate`
      proposes **nothing at all** for a new module until it is listed there —
      silent, and indistinguishable from "no schema changes". The template
      README's step 4 now says so too
- [x] CI smoke test guards the generator itself — two steps in the `test`
      job: a real `make new-module NAME=zztest` in the real checkout
      (placeholder grep, ruff, `mypy --strict`, its own pytest run, then
      deleted in a trap), plus the three refusal cases. The smoke has to run
      in the real tree rather than a temp workspace because mypy and pytest
      both need `app.modules.zztest` genuinely importable; it deletes the
      module in the same step because while it exists its `Item` is on the
      process-wide `SQLModel.metadata` for anything that sweeps
      `app.modules.*` — the alembic placeholder guard covers the
      *un*generated scaffold only, by design. `backend/tests/test_new_module_script.py`
      (59 tests) covers everything that doesn't need a real checkout, against
      a scratch backend root in `tmp_path`
- Three decisions the brief didn't settle. (1) `NAME` is capped at 27
      characters, which is not arbitrary: the scaffold's composite index is
      `ix_<name>_item_org_id_status_created_at_id`, 36 characters of fixed
      text, and Postgres truncates identifiers to 63 bytes *silently* — a
      longer name would not fail, it would collide with the next long name
      months later. The test re-derives the limit from the rendered template
      rather than trusting the arithmetic. (2) Reserved names are read out of
      the tree (module directories, `ModuleName`'s `Literal` members parsed
      with `ast`, Python keywords) instead of being listed in the script, so
      `NAME=file_storage` is refused even though no such directory exists.
      The parse rather than an import keeps the generator runnable from a
      bare checkout with no installed `app` and no `.env`. (3)
      `docs/runbooks/new-module.md` follows T-252's not-yet-written runbook
      template structure (trigger · pre-checks · steps with expected output ·
      verification · rollback · escalation) rather than inventing a shape
      T-252 will then have to retrofit

---

### T-252 ADR + runbook templates

> needs: —

**Goal** Decisions and operations each have a standing document type with a template, an index, and a lifecycle — so the docs habit survives contact with deadlines.

**Build**

- `docs/adr/_template.md`: number/title · status (`proposed` / `accepted` / `superseded by ADR-NNN`) · date · context · decision · consequences (positive **and** negative) · alternatives considered. Kept to one page — an ADR that takes >30 min to write doesn't get written
- `docs/adr/README.md`: index table (number · title · status · date) + the rule for what deserves an ADR: any decision that is expensive to reverse or that a future maintainer will ask "why on earth" about
- Backfill the ADRs this plan already owes, even if brief: 0001 security baseline (T-213), 0002 PgBouncer (T-244), 0003 read replica (T-246), 0004 marketing framework choice (placeholder — decided in T-300)
- `docs/runbooks/_template.md`: trigger (when to reach for this) · pre-checks · steps with expected output per step · verification · rollback · escalation. Retrofit heading structure onto the existing runbooks (deploy-render, module-toggle-guide, degraded-mode, maintenance-mode, etc.) without rewriting their content
- CI check: every `docs/adr/*.md` (except template/README) carries a status line; index is regenerated or verified by script so it can't drift

**Tests**

- CI: ADR status-line + index-consistency script green; a statusless ADR fails it

**Done when**

- [x] Both templates committed; ADR index live — `docs/adr/_template.md`
      (status · date · context · decision · consequences · alternatives, one
      page, with the "if it takes >30 min it doesn't get written" rule stated
      in the template itself) and `docs/runbooks/_template.md`. `docs/adr/README.md`
      carries the what-deserves-one rule (expensive to reverse, **or** a future
      maintainer asks "why on earth"), what explicitly doesn't, the
      `Proposed → Accepted → Superseded by ADR-NNNN` lifecycle, and an index
      generated between markers rather than hand-maintained
- [x] Owed ADRs exist (0001–0004) — 0001–0003 were already written, and each
      gained the `**Date:**` line the index reads plus the
      `## Alternatives considered` the template requires. That last section was
      not filler: the material was already inline in both 0002 (the pooling-mode
      table, `NullPool`, pgcat/Odyssey) and 0003 (the "not by HTTP verb"
      argument) — it just wasn't findable by anyone asking "was X considered".
      0004 is the placeholder T-300 replaces: no framework picked, but the four
      constraints that decide it recorded while they are still visible, of which
      one is the real asymmetry — `packages/ui` ships **zero** `"use client"`
      directives (verified, not assumed), so an RSC-first framework cannot
      consume the shared component package as it stands and Astro's islands can
- [x] Existing runbooks follow the template's heading structure — ten
      retrofitted to `Trigger · Pre-checks · Steps · Verification · Rollback ·
      Escalation`, keeping their own extra sections (a runbook that needs a
      per-integration behaviour table between Trigger and Steps should have
      one, so extras are allowed anywhere and only the six are ordered). Four
      documents in `docs/runbooks/` are not procedures and carry
      `<!-- runbook-template-exempt: <reason> -->` instead: `log-shipping.md`
      (reference), `pentest-prep.md` (engagement inventory), and the two
      `phase*-verification.md` files (records of passes that already ran).
      Writing the missing sections was where this stopped being clerical —
      "what is the point of no return" has a real answer in five of the ten
      (key rotation's step 5, a restore's cutover soak, a finalized Stripe
      invoice, the deleted `TOTPSecret`, the migration that runs before the
      deploy it can't be rolled back with) and none of them were written down
- [x] CI check green, and a statusless ADR fails it —
      `backend/scripts/check_docs.py`, wired into the `lint` job and
      `make lint`; `make check-docs ARGS=--write` regenerates the index.
      `backend/tests/test_check_docs.py` (39 tests) pins each rule against a
      scratch `docs/` tree in `tmp_path`, plus two that guard the real thing:
      one runs the checker over this repo's actual `docs/`, and one asserts
      each `_template.md` would itself pass the rules it documents — a template
      that drifts from its own checker is worse than no template
- Four decisions the brief didn't settle. (1) The date lives as a `**Date:**`
      line *inside* `## Status`, not as its own section or a `Accepted — date`
      suffix: ADR-0003's status is a qualifying paragraph ("Accepted — plumbing
      only. **No replica is provisioned**…"), and that prose is worth more than
      a parseable one-liner, so the parser takes the first non-empty line for
      the status word and finds the date on its own line. (2) The script checks
      ADR *section presence and order* as well as the status line the brief
      named — free once the sections exist, and it is what stops the template
      from being advisory. It deliberately stops there: it cannot check whether
      the prose is true, which is why README states the "ADR ships in the same
      PR as the change" rule as the thing no script enforces. (3) The runbook
      exemption is an in-file marker with a mandatory reason (same shape as the
      existing `rtl-ok:` / `impersonation-ok:` comments) rather than a list
      inside the script, so the reason sits where the reader is. (4) The six
      runbook headings are all `##`, following `encryption-key-rotation.md`'s
      existing `## Trigger` rather than `new-module.md`'s bold `**Trigger**`
      lead — a heading is checkable and a bold line is not, so T-251's runbook
      got the one-line retrofit instead of the other nine getting the loose
      shape

---

## P3-G · Support tooling & operator features

> Everything here hardens operational quality: safer impersonation, better support-tool integration, global product controls.

### T-260 Impersonation destructive-action blocklist (server-side)

> Hardens T-142. Applies uniformly to web and mobile impersonation sessions.
> needs: T-142

**Goal** During an active impersonation session, the server refuses actions that would produce user-visible side effects the real user didn't intend. Prevents a support agent from accidentally sending an email, marking notifications read, changing password, or issuing charges _as_ the real user.

**Build**

- `backend/app/core/dependencies.py` — add `reject_during_impersonation(action_kind: Literal["destructive","notifying","billing","data_mutation"])`:
  ```python
  def reject_during_impersonation(kind: str) -> Callable:
      async def checker(request: Request):
          if request.state.principal.impersonator_id:
              raise PermissionDeniedError(
                  f"Action '{kind}' is blocked during impersonation. "
                  f"End impersonation to perform this action as yourself."
              )
      return checker
  ```
- **Blocklist categories** and where they apply:
  - `notifying` — outbound email/push sends (`/notifications/*`, `/auth/forgot-password`, `/auth/magic-link`, invitation resends). Also: silent suppression of push notifications on the impersonated user's registered devices for any action performed during impersonation
  - `billing` — checkout session creation, subscription changes, portal session, any endpoint that could produce a charge
  - `data_mutation` — password change, 2FA disable, email change, account deletion, org deletion
  - `notification_read_state` — marking notifications read (silent state pollution)
- Impersonation-token JWTs carry `impersonator_id` claim (already in T-142); the dependency checks presence of that claim
- Every blocked attempt writes an audit event with `action="impersonation.blocked"` — useful signal for support-tool improvement

**Tests**

- Impersonation session attempting `POST /auth/forgot-password` → 403; audit event written
- Impersonation session attempting `POST /billing/checkout-session` → 403
- Impersonation session attempting `PATCH /notifications/{id}/read` → 403
- Non-impersonation session performing same actions → succeeds
- Every destructive endpoint is covered by the parametrised test matrix (mirror of T-049 cross-tenant sweep)

**Done when**

- [x] Every destructive endpoint in the codebase has `Depends(reject_during_impersonation(...))` or an explicit `# impersonation-ok: <reason>` comment —
      all 52 mutating routes decided: 43 guarded, 9 marked. The marked nine are
      the login/logout legs (`/auth/login`, `/refresh`, `/logout`,
      `/magic-link/consume`, `/2fa/login`, `/webauthn/login{,/options}`), the
      two token-only public flows (`/auth/register`, `/auth/verify-email`), the
      Stripe webhook (server-to-server, no bearer token), and
      `/admin/impersonate/end` — blocking that last one would trap the agent
      inside the identity they are trying to leave.
      `test_every_mutating_route_is_decided` walks the real route table and
      fails on a write route added with neither, so this can't rot; the
      scaffold in `app/modules/_template/router.py` carries the dependency on
      its three write routes, so a module generated by `make new-module`
      (T-251) passes that sweep the moment it exists
- [x] Parametrised block-matrix test covers every category —
      `backend/tests/security/test_impersonation_blocklist.py` (61 tests). The
      matrix is *generated from the route table* rather than hand-listed, so
      adding a guard adds a case: every one of the 44 guarded routes is hit
      with a real impersonation token and must answer 403
      `errors.impersonation_blocked` carrying the category the route claims.
      Bodies are empty and path parameters are throwaway UUIDs on purpose —
      the guard is a route-level dependency, so FastAPI solves it before it
      validates anything, and a 403 rather than a 422 is itself the proof the
      block lands ahead of the permission, entitlement and step-up gates
- [x] Push notification suppression verified end-to-end —
      `NotificationService.send()` drops the push channel (only the push
      channel) whenever `app.core.audit_context`'s impersonator ContextVar is
      set, and records `push_suppressed: true` on the `notification.sent`
      audit event. Verified through a real request behind the production
      `ImpersonationContextMiddleware`: the blocklist means no reachable
      endpoint both survives an impersonation session and sends a
      notification, so the test mounts a one-route probe app on the real
      middleware + the real service, which is the only synthetic part
- Four decisions the brief didn't settle. (1) The guard reads the
      `Authorization` header itself rather than `request.state.principal`
      (which doesn't exist here) or the ContextVar the middleware already
      sets. Both alternatives fail *open* if that middleware is ever
      reordered or a route is mounted on a sub-app, and this dependency is the
      only thing between a support agent and a charge on someone else's card.
      It also deliberately avoids `Depends(oauth2_scheme)`: that would attach
      a `security` requirement to the public endpoints it guards, and every
      generated client would then believe `/auth/forgot-password` needs a
      token (pinned by
      `test_guarding_a_public_route_does_not_make_it_look_authenticated`).
      (2) The guard commits its own audit event before raising. `AuditService`
      writes in a savepoint inside the caller's transaction, and the 403
      unwinds `get_session`'s `except: rollback()` — so without the commit the
      "every blocked attempt writes an audit event" requirement was true in a
      test using an overridden session and false in production. The same
      latent bug was already in `require_permission`'s denial event (T-219);
      fixed there in the same change. (3) Categories are the four from the
      brief's blocklist section, not the four in its signature sketch —
      `destructive` never appears in either the categories list or the route
      assignments, and an unused member of a closed `Literal` is a rename
      waiting to go half-done (`test_every_category_is_actually_used` fails if
      one stops being applied). (4) No ADR: `docs/adr/README.md` explicitly
      excludes "anything already enforced and explained by a test", which is
      exactly what the sweep plus the `# impersonation-ok:` reasons are

---

### T-261 Mobile impersonation via deep-link handoff

> needs: T-142, T-260, T-047

**Goal** Superadmin reproduces mobile-specific issues on their own device without needing physical access to the user's phone.

**Build**

- Backend endpoint `POST /api/v1/admin/impersonate/mobile-handoff`:
  - Requires an active impersonation session (must have already called T-142's impersonate endpoint)
  - Issues a one-time exchange token (short random string, 2 minute TTL, Redis-stored keyed by hash) that maps to the impersonation JWT
  - Returns `{ deep_link: "forge://impersonate?token=<exchange_token>&user_email=<email>", expires_at }`
- Admin web UI adds "Handoff to my mobile" button next to existing "Impersonate" button — displays the deep link as a QR code + copyable text
- Mobile app:
  - Deep-link handler for `forge://impersonate` navigates to a `ImpersonateConfirmScreen`
  - Confirmation screen displays the user email (from the query param — informational only, real identity comes from the exchange), the reason from the original impersonation request, and duration remaining
  - "Confirm" calls `POST /api/v1/admin/impersonate/mobile-exchange` with the exchange token → receives the actual access token → stores in secure store with a `IS_IMPERSONATION_SESSION` flag
  - "Cancel" invalidates the exchange token immediately
- Mobile app during impersonation:
  - Persistent red banner across every screen: "Impersonating <email> · <mm:ss remaining> · [End]"
  - Banner is non-dismissable
  - Push notification registration for the impersonated user is _not_ updated to the superadmin's device (prevents leaking pushes)
  - App state does not persist across cold restart when in impersonation mode (secure-store flag is cleared on app launch if it exists)
  - "End impersonation" clears secure store and returns to login (not to the superadmin's previous session — mobile has one session per install)

**Tests**

- Exchange token single-use (second attempt to exchange → 401)
- Exchange token expires in 2 minutes (freezegun-advance → 401)
- Cold-restart clears impersonation state
- Banner visible on every screen
- Push notification triggered by impersonated user's normal activity fires to their real devices, not the superadmin's device
- Destructive actions blocked (verified via T-260's blocklist)
- Audit event `impersonation.mobile_handoff` records the exchange
- Audit event `impersonation.mobile_ended` records when superadmin ends

**Done when**

- [ ] Handoff works end-to-end from admin web to iOS Simulator + Android emulator
- [ ] Push-suppression test proves no leak to superadmin device
- [ ] Runbook `docs/runbooks/mobile-impersonation.md` documents when to use mobile handoff vs regular impersonation vs screen-share alternatives

---

### T-262 Support integration hook (SupportContext)

> needs: T-021 (get_current_user), T-025 (org context)

**Goal** A thin, tool-agnostic layer that surfaces the user's context to whichever support tool a product picks (Intercom, Crisp, Plain, HelpScout, or none). The template never depends on a specific tool.

**Build**

- `packages/hooks/src/useSupportContext.ts`:

  ```typescript
  export interface SupportContext {
    userId: string;
    email: string;
    fullName: string | null;
    orgId: string;
    orgName: string;
    plan: string | null;
    subscriptionStatus: string | null;
    signupAt: string;
    locale: string;
    isImpersonating: boolean; // suppress support-tool identity if true
  }

  export function useSupportContext(): SupportContext | null;
  ```
  - Returns null while auth is loading, so support widgets don't flash unauthenticated
  - Returns `isImpersonating: true` when in an impersonation session — the wrapper below should suppress identity in that case so the superadmin's actions don't pollute the real user's support history

- Product-side wrapper example (documented, not implemented in template): `apps/web/src/features/support/SupportWidget.tsx` reads `useSupportContext()` and calls e.g. `window.Intercom("boot", { user_id: ctx.userId, ... })`
- Backend endpoint `GET /api/v1/me/support-context` — same fields, returned via API for the mobile app (which cannot use JavaScript widgets; usually opens a `mailto:` with pre-populated context or launches a chat app SDK)
- `product.config.py` adds:
  ```python
  SUPPORT_TOOL: Literal["none", "intercom", "crisp", "plain", "helpscout", "custom"] = "none"
  SUPPORT_URL: str | None = None    # fallback: opens as mailto: or web URL
  ```
  Web app renders a "Contact support" link/button in the sidebar; behaviour depends on `SUPPORT_TOOL`

**Tests**

- `useSupportContext()` returns null while unauthenticated
- During impersonation: `isImpersonating: true`
- Mobile: `GET /me/support-context` returns fields correctly
- Cross-tenant safety: user in org A cannot receive org B's context

**Done when**

- [ ] Hook + endpoint working
- [ ] `product.config.py` `SUPPORT_TOOL` option honoured on web + mobile
- [ ] Docs `docs/runbooks/support-tool-integration.md` show how to wire Intercom, Crisp, Plain

---

### T-263 ProductSettings singleton — global runtime toggles

> needs: T-140 (superadmin guard), T-063 (public config endpoint)

**Goal** Global switches an operator can flip without a redeploy: maintenance mode, feature-wide kill switches, banner announcements. Distinct from `product.config.py` (deploy-time) and `OrgFeatureFlag` (per-org).

**Build**

- `backend/app/modules/product_settings/models.py`:
  ```python
  class ProductSettings(SQLModel, table=True):
      id: int = Field(default=1, primary_key=True)   # singleton — always 1
      settings: dict = Field(sa_column=Column(JSONB, default=dict))
      updated_at: datetime = Field(default_factory=utcnow)
      updated_by: UUID | None
  ```
  Migration ensures a single row with id=1 exists on first upgrade.
- `ProductSettingsService`:
  - `get(key: str) -> Any` — Redis-cached with 60s TTL, invalidated on write
  - `set(key: str, value: Any, actor: User) -> None` — superadmin only, audit-logged
  - Well-known keys (documented, not hard-coded): `maintenance_mode: bool`, `maintenance_message: str`, `signup_disabled: bool`, `global_banner: {type, message, dismissible}`, `emergency_kill_switch: dict[str, bool]` (per-module)
- Middleware `check_maintenance_mode` (registered before routers):
  - If `maintenance_mode=True`, all non-admin, non-health endpoints return 503 with `{"error": {"code": "maintenance", "message": settings.maintenance_message}}`
  - Admin endpoints and `/health/*` remain reachable so operators can end maintenance
- `GET /api/v1/config` (from T-063) extended: includes `banner: {...}` if `global_banner` set; the frontend renders it above the app shell
- Admin UI: dedicated `/_admin/settings/product` page with editable form for well-known keys + raw JSON edit for advanced use

**Tests**

- Toggle `maintenance_mode=True` → all user endpoints return 503; admin endpoints remain 200; health remains 200
- Global banner appears in `GET /config`; disappears on unset
- Cache invalidation: setting a value updates `GET /config` within 60s (or immediately with explicit invalidation)
- Non-superadmin `PATCH /admin/settings/product` → 403
- Audit event written on every settings change with diff

**Done when**

- [ ] Maintenance mode works end-to-end (test flip and verify user impact)
- [ ] Banner shows/hides via GET /config on web + mobile
- [ ] Runbook `docs/runbooks/maintenance-mode.md` documents the incident-response flow
