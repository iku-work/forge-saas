# Outbound webhooks: endpoints, deliveries, replay (T-320, T-321, T-322)

Operating the `outbound_webhooks` module — the events this product pushes to
a customer's URL, and what to do when they stop arriving. The design is in
`backend/app/modules/webhooks/models.py` (why the delivery log is the
fastest-growing table here), `signing.py` (the signature scheme, and the one
rule that costs people days), and `backend/app/workers/jobs/webhooks.py` (the
retry ladder and why it is explicit rather than ARQ's).

The module ships **off**, and requires `billing`. A product that has never
turned it on has no `webhook_endpoint` or `webhook_delivery` table and
nothing here applies.

## Trigger

- A customer says "we stopped receiving events" — start at
  [Diagnose a delivery](#2-diagnose-a-failed-delivery).
- An endpoint was auto-disabled after repeated failures and the owner wants it
  back — [Re-enable](#3-re-enable-an-auto-disabled-endpoint).
- A signing secret has leaked — [Rotate](#4-rotate-a-signing-secret), which is
  the only step here with a clock on it.
- `webhook_delivery` is growing faster than expected —
  [Retention](#5-check-retention).

**Not this runbook:** a *Stripe* webhook arriving *at* this product
(`app/modules/billing/webhooks.py` — inbound, different direction, different
table), or an integration that authenticates with an API key
(`docs/runbooks/api-keys.md`).

## Pre-checks

- `outbound_webhooks` is `True` in `backend/product/config.py` **and**
  `outboundWebhooks: true` in `product.config.ts`. Both, always: a
  half-applied toggle is a backend that 404s and a UI that still offers the
  page.
- The migration has been applied: `python -m app.core.migrations plan` shows
  `outbound_webhooks -> enabled  -> applying`.
- The **worker** is running. Nothing is delivered by the API process; a
  product with webhooks enabled and no worker has a `pending` queue and no
  symptom until somebody looks.
- You hold the org-manage permission (owner or admin). The delivery log
  carries payloads, so it is not a member-level surface.

## Steps

### 1. Register an endpoint

In the app: **Settings → Webhooks → Add endpoint**. Or:

```bash
curl -sX POST https://app.example.com/api/v1/webhooks/endpoints \
  -H "Authorization: Bearer $SESSION_TOKEN" \
  -H "X-Org-ID: $ORG_ID" \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://consumer.example.com/hooks/forge",
       "event_types":["member.invited","member.joined"]}'
```

The response carries the signing secret **once**. It is stored encrypted and
no later read returns it; a lost secret is a rotation, not a support ticket.

The URL is checked against the outbound guard (`app/core/url_guard.py`) here
*and again immediately before every delivery* — a hostname that resolves
publicly today can resolve to `169.254.169.254` tomorrow, and DNS belongs to
whoever registered the URL.

### 2. Diagnose a failed delivery

**Settings → Webhooks → Deliveries** on the endpoint, filtered to `Failed` or
`Given up`. Each row carries the response code and the (truncated) response
body, which is almost always the answer: a 401 is the consumer rejecting the
signature, a 404 is a URL that moved, a timeout is a consumer doing work
inline that should be queued.

The retry ladder is **1 m, 5 m, 30 m, 2 h, 12 h**, then dead-lettered — six
attempts over roughly fifteen hours. A consumer that was down for an hour has
already received the event by the time anyone asks; a delivery still marked
`pending` with `next_attempt_at` in the future is not a problem yet.

If the consumer says the signature does not verify, the cause is nearly always
that they are verifying a **re-serialised** body. The signature covers the
exact bytes on the wire:

```
X-Forge-Signature: t=<unix seconds>,v1=<hex hmac-sha256 of "{t}.{raw body}">
```

`app/modules/webhooks/signing.py::verify_signature` is a working reference
implementation, including the 5-minute timestamp tolerance — point them at it
rather than describing it.

### 3. Re-enable an auto-disabled endpoint

Five consecutive **dead-lettered** deliveries switch an endpoint off and
notify the organization's owners (guarded — a product with `notifications`
off gets the audit event and the disabled row, and no email). The UI shows
"Switched off after 5 failures" rather than a bare "Disabled", because those
are different facts.

Fix the consumer first, then **Enable**. Re-enabling clears
`consecutive_failures`, which matters: without that, the next single failure
would trip the breaker again immediately and read as "your fix did not work".

Deliveries dead-lettered while it was off are not resent automatically —
[replay](#6-replay-a-delivery) the ones that matter.

### 4. Rotate a signing secret

**Settings → Webhooks → Rotate secret**, or
`POST /api/v1/webhooks/endpoints/{id}/rotate-secret`.

The new secret is returned once. The previous one keeps working for **24
hours**, and every request in that window is signed with *both* — so the
consumer can deploy a verifier that accepts either, switch, and drop the old
one, with no moment where correctly-signed requests are rejected. The UI
shows the deadline; so does the API response
(`previous_secret_expires_at`).

If the secret leaked, tell the consumer the deadline is now: rotate, have them
deploy, and rotate a second time to end the overlap immediately.

### 5. Check retention

`webhook_delivery` is one row per event per endpoint, each carrying the
payload that was sent. `WEBHOOK_DELIVERY_RETENTION_DAYS` (default **30**)
is what keeps it bounded; the prune job runs at 03:00 UTC and deletes in
batches so it never takes a long lock.

```sql
SELECT count(*), min(created_at) FROM webhook_delivery;
```

If the oldest row is much older than the retention window, the prune job is
not running — check the worker, and check that the module is enabled *in the
worker's* configuration (the job no-ops when it is not).

### 6. Replay a delivery

**Settings → Webhooks → Deliveries → Replay**, or
`POST /api/v1/webhooks/deliveries/{id}/replay`.

A replay sends the **stored** payload — what happened then, not what the row
it described says now — as a **new** delivery with a new `X-Forge-Delivery`
id. The original row is never modified: the log is an audit trail, and "we
replayed it and now it says delivered" is a sentence this API cannot produce.

Retries of one delivery share a `delivery_id` so a consumer can dedupe them; a
replay deliberately does not, because it is a request to process the event
again.

## Verification

- `GET /api/v1/webhooks/endpoints` lists the endpoint, `enabled: true`,
  `consecutive_failures: 0`.
- A test event arrives at the consumer within seconds, with
  `X-Forge-Event`, `X-Forge-Delivery` and `X-Forge-Signature` headers.
- The delivery row reads `delivered`, `attempt_count: 1`, `response_code: 200`.
- `forge_webhook_queue_depth` (Prometheus, published by the worker through
  Redis) is at or near zero. A depth that climbs and does not fall is a
  worker that is not consuming, not a slow consumer.
- `forge_webhook_deliveries_total{outcome="dead"}` is flat.

## Rollback

- **An endpoint you should not have registered:** disable it (immediate, and
  reversible) or delete it (permanent — the delivery log goes with it, by
  `ON DELETE CASCADE`).
- **A rotation done in error:** you cannot un-rotate. The previous secret is
  still valid for the rest of the overlap window, so the consumer is not
  broken; rotate again and distribute the new secret.
- **Turning the module back off:** endpoints and deliveries stay in their
  tables (that is what `MODULE_TABLES` is for), the routes disappear, and the
  delivery job refuses to send for any row it finds — a queued delivery is
  dropped rather than delivered. Turning it back on resumes delivery for
  everything still `pending`, which is worth saying out loud before you flip
  it twice.

## Escalation

**A consumer receives an event twice.** Expected, and documented: delivery is
at-least-once. A retry after a response this side never saw (a timeout on a
request the consumer processed) is the ordinary case. `X-Forge-Delivery` is
the idempotency key — it is stable across retries of one delivery precisely so
a consumer can dedupe on it.

**Deliveries stuck `pending` with `next_attempt_at` in the past.** The
deferred ARQ job was lost, which happens when Redis restarts without
persistence. `sweep_due_webhook_deliveries` re-enqueues them every five
minutes; if they are still stuck after that, the worker is not running.

**An endpoint's URL now resolves to a private address.** The delivery is
marked `dead` immediately with `unsafe destination` in `error`, and is not
retried — retrying would be five more requests at an internal address. This is
working as intended; the customer has to fix their DNS or register a
different URL.

**Nothing is delivered and there are no failed rows either.** Check that
anything is *emitting*: the registry is
`backend/app/modules/webhooks/events.py`, and
`tests/modules/test_webhook_events.py` pins that every registered event has a
call site. If the endpoint is subscribed to an event this product never
emits, the delivery log is correctly empty.
