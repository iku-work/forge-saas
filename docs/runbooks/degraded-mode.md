# Degraded-mode runbook (T-206)

A circuit breaker guards every outbound call to a third party (Stripe,
Resend, S3/R2/MinIO, the virus scanner, Expo push): `backend/app/core/resilience.py`.
A slow or dead vendor degrades one feature — it never cascades into
request-path timeouts or a worker pile-up.

## Trigger

Any one of:

- users report one feature failing while the rest of the app is fine, with a
  503 envelope (`code: "service_unavailable"`) naming the feature — "Billing",
  "Email delivery" — rather than the vendor;
- `forge_breaker_state{integration="…"}` reads `1` (half-open) or `2` (open)
  in Prometheus;
- `resilience.breaker_open: integration=<name>` in the backend or worker logs.

Nothing pages on this today: the three metrics below are exported, but
`docs/grafana/alerts.yml` has no rule watching them, so in practice this
runbook starts from a user report or a 503 in the logs.

Not this runbook: a 503 with no breaker open (that is the app itself, or the
`/health/ready` dependency check), or a slow-but-succeeding integration —
`docs/runbooks/slow-queries.md` for the database, the vendor's own status page
for the rest.

## How it works

- One named `pybreaker.CircuitBreaker` per integration
  (`stripe` / `resend` / `s3` / `virus_scanner` / `expo_push`), each shared by
  every call site that talks to it. `fail_max=5` consecutive failures opens a
  breaker; `reset_timeout=30s` later, the next call is let through as a
  half-open probe — one success closes it again.
- Before a failure counts against a breaker at all, it has to survive a
  tenacity retry (exponential 0.5s → 4s backoff, a few attempts) — and it
  only retries **transient** failures (timeouts, connection errors, a
  de-facto 5xx). A business/validation error (a bad card, an unknown Stripe
  price, a 400 from Resend) is never retried and never counts against the
  breaker; pybreaker treats it as a success for its own bookkeeping.
- Once a breaker is open, every call raises `ExternalServiceUnavailableError`
  (a 503) immediately, without even attempting the call. The message names
  the degraded *feature* ("Billing", "Email delivery", …), never the vendor.

## Per-integration behavior when the breaker is open

| Integration | Trigger | What happens |
|---|---|---|
| **Stripe** | checkout, portal, customer creation, invoice listing | The request-path endpoint returns a 503 envelope (`code: "service_unavailable"`). Webhooks are unaffected — Stripe itself retries a failed webhook delivery for days, and webhook processing (`billing/service.py`'s `handle_event`) makes no outbound Stripe calls. |
| **Resend** | `send_email` job | The whole ARQ job re-queues itself (`arq.worker.Retry`, deferred ≥30s) instead of dropping the email. Emails are delayed, never dropped. A permanent (4xx) failure is logged + reported to Sentry and the job still completes normally — that's a bad recipient/template, not an outage. |
| **S3 / R2 / MinIO** | presign upload/download, HEAD, delete | The request-path endpoint returns 503; existing file metadata is unaffected (no partial writes). |
| **Virus scanner** | `virus_scan` job | The file stays `status="ready"` (pending scan, undownloadable) and the whole job re-queues (`arq.worker.Retry`). Only a scanner failure that exhausts its retries *without* tripping the breaker marks the file `"failed"`. |
| **Expo push** | `send_push` job | The push is dropped after retries — logged + reported to Sentry, job completes normally, no re-queue. In-app and email channels for the same notification are unaffected. |

## Reading the dashboard / alerts

Three metrics, labeled by `integration`:

- `forge_breaker_state{integration}` — gauge, `0` closed / `1` half-open /
  `2` open.
- `forge_breaker_open_total{integration}` — counter, incremented every time
  a breaker trips.
- `forge_external_call_seconds{integration}` — histogram of the whole
  `resilient()`-wrapped call (including its own inline retries).

## Pre-checks

- Access to `/metrics` (or the Prometheus/Grafana it feeds) and to
  backend + worker logs. `/metrics` is blocked externally by Caddy in
  production (`Caddyfile.prod`), so this means the internal network or the
  hosting platform's own log/metrics view.
- The vendor status pages open in a tab: Stripe, Resend, AWS/Cloudflare, Expo.
  Step 2 is checking them, and finding the URL under pressure is wasted time.
- **Nothing to change yet.** The first two steps are read-only on purpose; a
  breaker is a symptom and the reflex to restart something is the main way
  this gets worse.

## Steps

1. **Confirm which integration tripped.** Check `forge_breaker_state` in
   Prometheus/Grafana, or grep worker/backend logs for
   `resilience.breaker_open: integration=<name>` (logged once per trip).
2. **Check the vendor's own status page** for Stripe/Resend/AWS/Cloudflare
   before assuming it's our bug — a breaker tripping is usually a symptom,
   not the root cause.
3. **Let it recover on its own first.** `reset_timeout` is 30s — a genuinely
   transient blip self-heals without intervention; don't restart the worker
   or backend to "fix" an open breaker, that doesn't change anything the
   breaker itself doesn't already do on the next call.
4. **If it's still open after several minutes**, the vendor is actually down
   or misconfigured (bad API key, revoked webhook secret, wrong bucket
   region). Fix the underlying config/outage; the breaker will close itself
   on the next successful call once it does.
5. **Queued jobs (Resend, virus scanner)** aren't lost — they're sitting in
   ARQ's deferred queue and will re-attempt automatically. Check
   `forge_arq_queue_depth` (T-202) if you want to confirm they're piling up
   rather than draining.

## Verification

The breaker is closed again when `forge_breaker_state{integration="…"}` reads
`0` and a real request through the affected feature succeeds — check both. The
gauge alone can read `0` on a breaker that has simply not been called since it
reset, which looks identical to recovery and is not.

For the queue-backed integrations (Resend, virus scanner), recovery also means
the backlog drained: `forge_arq_queue_depth` (T-202) trending down rather than
flat. A closed breaker with a queue that never drains means the jobs are
failing for a second reason.

### Local verification

There's no dedicated `make` target for this — it's exercised directly in the
test suite (`backend/tests/test_resilience.py` for the breaker/retry
mechanics themselves; `test_email_job.py`, `test_virus_scan_job.py`,
`test_send_push_job.py`, `test_storage_backend.py`, `test_billing_checkout.py`
for each integration's own degraded-mode behavior). To reproduce manually
against the dev stack, point an integration's env var at an unreachable
endpoint (e.g. `AWS_ENDPOINT_URL=http://127.0.0.1:1` for S3) and make 5
requests through the affected feature — the 6th should come back as a 503
naming the feature, and `forge_breaker_state{integration="s3"}` should read
`2` on `/metrics`.

## Rollback

Nothing to roll back — this runbook changes nothing by design. The breaker
closes itself on the first successful call after `reset_timeout`, and every
step above is either a read or a fix to the underlying config/outage. Roll
*that* back the way you would any other config change.

The one thing that needs undoing is a change made *to* the breaker: if you
raised `fail_max` or `reset_timeout` to ride out an incident
(`backend/app/core/resilience.py`), put them back once the vendor recovers. A
breaker tuned for an outage is a breaker that won't trip during the next one.

## Escalation

- **Breaker still open after ~15 minutes with the vendor's status page
  green.** It is our configuration, not their outage: a revoked API key, a
  rotated webhook secret, a wrong bucket region, an expired token. Check the
  full exception in Sentry — the breaker's own log line records that it
  tripped, not why the underlying calls failed.
- **Two or more breakers open at once.** Almost never two simultaneous vendor
  outages; look for the shared cause underneath — egress/DNS from the
  container, a proxy, an expired CA bundle, the host being out of sockets.
- **The queue is growing faster than it drains** after the breaker closes
  (`forge_arq_queue_depth` flat or rising). The retry backlog is now its own
  problem: scale the worker or pause the producing path, and treat it as a
  capacity incident rather than a vendor one.
- **Emails or pushes were dropped, not delayed.** Only the Expo push path
  drops (see the table above) — if user-visible messages were lost, that is a
  communication decision, not an operational one; escalate to whoever owns the
  product, with the time window from the logs.
