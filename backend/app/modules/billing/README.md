# billing

Subscriptions, Stripe webhooks, checkout/portal sessions, plan-gated feature
entitlements, dunning, and usage-based metering.

## Public interface

- `app.modules.billing.service.BillingService` — webhook event dispatch,
  checkout/portal session creation, Stripe customer lifecycle.
- `app.modules.billing.dependencies.require_feature(feature, minimum=None)` —
  FastAPI dependency that gates a route on the org's current Plan
  entitlement.
- `app.modules.billing.dependencies.record_usage(metric, quantity=1)` —
  FastAPI dependency that meters a route (see below).
- `app.modules.billing.usage_service.UsageService` — record/aggregate usage
  directly (outside a route, e.g. from a background job).

## Events emitted

- Audit events (`app.modules.audit.service.AuditService`): `billing.payment_failed`,
  `billing.dunning_email_sent`, `trial.warning_sent_3d`, `trial.warning_sent_1d`,
  `billing.trial_expired_email_sent`.
- `send_email` jobs enqueued for dunning and trial-warning sequences.

## Usage-based billing (T-132)

`UsageRecord` is an append-only ledger: one row per billable event. Nothing
updates or deletes a row (correct a bad one by writing a compensating
record). `report_usage_to_stripe` (`app.workers.jobs.usage`, scheduled every
15 minutes) sweeps unreported rows, sums them per `(org_id, metric)`, and
reports each sum to Stripe as a
[billing Meter event](https://docs.stripe.com/billing/subscriptions/usage-based/meters).

### Adding a new metered metric

1. **Create the Stripe Meter** (dashboard or API) for the metric, e.g.
   `api_calls`. Note its `event_name`.
2. **Create a metered Price** on the Plan's Stripe product, attached to that
   Meter.
3. **Configure the Plan row**: add the mapping to
   `Plan.usage_meter_event_names`, e.g.
   `{"api_calls": "api_calls_meter"}`. One entry per metric the plan bills
   for; a metric absent from this dict is never reported for that plan (the
   job logs and skips it, retrying on the next tick once configured).
4. **Record usage from the feature code**, either:
   - as a route dependency: `Depends(record_usage("api_calls"))` (or
     `record_usage("api_calls", quantity=n)` for a non-1 unit); or
   - directly: `await UsageService(session, org_id).record(metric="api_calls", quantity=n)`,
     optionally with `idempotency_key=` if the caller might retry the same
     logical event (the DB unique constraint on
     `UsageRecord.idempotency_key` makes a retry return the original record
     instead of double-counting).
5. That's it — `report_usage_to_stripe` picks up unreported rows for the new
   metric automatically; no job or scheduler change needed.

### Reading current usage

`UsageService(session, org_id).aggregate(metric=..., since=..., until=...)`
sums quantity for an org/metric/time-window — e.g. for a "you've used X of Y"
display, combined with the entitlement from `Plan.features`.
