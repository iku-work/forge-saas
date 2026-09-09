# webhooks

Outbound webhooks: the events this product pushes to a customer's URL, signed,
retried, logged and replayable (T-320, T-321, T-322).

Gated by the `outbound_webhooks` module toggle, which ships **off** and
**requires `billing`**. Off, there are no `/api/v1/webhooks` routes, no
`webhook_endpoint` or `webhook_delivery` tables — and, unusually for a module
toggle, no *deliveries*: the job checks `is_enabled` before it sends, so a
delivery queued before the flip is dropped rather than pushed to a third party
after it.

Not to be confused with `app/modules/billing/webhooks.py`, which receives
Stripe's webhooks. Same word, opposite direction.

## Public interface

- `app.modules.webhooks.service.emit(session, org_id, event_type, payload)` —
  **the only entry point another module uses.** Safe to call unconditionally:
  it validates the payload against the registry, then returns immediately when
  the module is off or nothing is subscribed. A caller that must *query*
  something to build its payload should check
  `is_enabled("outbound_webhooks")` first, so a product with the module off
  pays nothing at all (see `TenancyService.change_role`).
- `app.modules.webhooks.events` — the event vocabulary: `EventType`, one
  payload model per event, and the descriptions the UI's picker and the
  published catalogue render from.
- `app.modules.webhooks.signing` — the signature scheme, **including a
  working consumer-side `verify_signature`**. Shipped rather than described:
  "verify the HMAC" is the instruction every integration guide gives and about
  half of them get subtly wrong.
- `app.modules.webhooks.service.WebhookEndpointService` /
  `WebhookDeliveryService` — management and the delivery job's bookkeeping.
- `app.modules.webhooks.router.router` — the management surface under
  `/api/v1/webhooks/`, session-only.

`WebhookEndpointRepository`, `WebhookDeliveryRepository`, `WebhookEndpoint` and
`WebhookDelivery` are internal.

## The four decisions worth knowing before editing anything here

**The signature covers the bytes that are sent.** `deliver_webhook` serialises
the payload exactly once, and those bytes are both signed and posted. Signing a
re-serialised dict — different key order, a space after a comma — makes every
consumer's verification fail with nothing in either log to say why. If you
change how the body is built, change it in one place.

**`secret` is `EncryptedString`, not a digest.** §7's rule for a `*_secret`
column, and here it is a genuine requirement rather than a convention: signing
means computing an HMAC with the value, so a hash would make the column
useless. It is walked by T-245's key-rotation job like every other encrypted
column.

**The retry ladder is explicit, and scheduled from the row.** 1 m, 5 m, 30 m,
2 h, 12 h, then dead-lettered — a documented contract with the consumer, not
ARQ's own backoff. `_defer_by` is computed from the row's `next_attempt_at`, so
the queue and the log cannot disagree about when the next attempt is.

**The breaker counts dead letters, not attempts.** One consumer restart
produces five failed *attempts* of the same delivery; an endpoint disabled by
that is an endpoint disabled by a deploy. Five distinct deliveries that all
gave up is a consumer that is gone.

## Events emitted

Audit events, via `AuditService`. None carries a secret — not the signing
secret, not a hash of it.

| action | when |
| --- | --- |
| `webhook_endpoint.created` | `WebhookEndpointService.create_endpoint` (diff: url, event types) |
| `webhook_endpoint.updated` | `WebhookEndpointService.update_endpoint` (diff: the changed fields) |
| `webhook_endpoint.secret_rotated` | `rotate_secret` (diff: when the previous secret expires) |
| `webhook_endpoint.deleted` | `delete_endpoint` |
| `webhook_endpoint.auto_disabled` | the delivery job, after N consecutive dead letters (severity `warning`) |
| `webhook_delivery.replayed` | `WebhookDeliveryService.replay` (diff: the original delivery id) |

## What this module owns elsewhere in the tree

| Where | What |
| --- | --- |
| `alembic/versions/outbound_webhooks/` | both tables, on their own branch |
| `app/workers/jobs/webhooks.py` | the delivery job, the due-delivery sweep and the retention prune |
| `app/workers/scheduler.py` | those last two, at 03:00 UTC and every five minutes |
| `app/core/metrics.py` | `forge_webhook_queue_depth`, `forge_webhook_deliveries_total` |
| `app/core/config.py` | `WEBHOOK_DELIVERY_RETENTION_DAYS`, `WEBHOOK_TIMEOUT_SECONDS` |
| `docs/runbooks/outbound-webhooks.md` | diagnosing, re-enabling, rotating, replaying |
| `docs/grafana/` | two dashboard panels and two alert rules |

## No cache

Deliberately. The dispatch query ("every enabled endpoint in this org
subscribed to this event") runs once per emitted event against a table with a
handful of rows per org and a covering index; caching it would add an
invalidation matrix to keep honest in exchange for nothing measurable. The
delivery log is never cached at all — it is read by a person debugging, and a
stale answer there is worse than a slow one.

## Retention

`webhook_delivery` is the fastest-growing table this template adds: one row per
event per endpoint, each carrying the payload that was sent.
`WEBHOOK_DELIVERY_RETENTION_DAYS` (default 30) and the batched prune job are
what keep it bounded. A delivery log with no retention policy is a disk-full
incident with a date on it.
