# Billing drift runbook

What to do when what Stripe is charging and what this system grants have come
apart: a customer who paid and was not upgraded, an org still on a paid plan
nobody is billed for, or one org paying twice. The design these procedures
assume — Stripe is the system of record, local state is a cache of it — is in
`app/modules/billing/service.py`'s module docstring.

## Trigger

Reach for this when any of these is true:

- **`forge_billing_unapplied_events_total` is non-zero.** An event was
  received, understood, and deliberately not applied. The `reason` label says
  which: `duplicate_subscription`, `unknown_price`, `missing_org_id`,
  `stale_event`.
- **`forge_billing_reconciled_total` has a sustained non-zero rate.** The
  hourly sweep is repairing drift, which means webhooks are being lost. The
  sweep hides the symptom, so nothing else will say so.
- **A `webhook_event` row has `processed_at IS NULL` and a rising
  `attempt_count`.** Stripe is retrying into a failure.
- **Stripe emailed you that the endpoint was disabled.** Stripe disables an
  endpoint that keeps failing; from that moment events are not retried, they
  are simply not sent.
- **A customer says they paid and did not get their plan.**

Not this runbook: a customer whose *card* failed is dunning, working as
designed — see the `dunning_campaign` table and
`app/workers/jobs/billing.py`'s `send_dunning_email`. A plan that grants the
wrong features is the plan catalogue, not drift — see
`docs/runbooks/stripe-usage-pricing.md` and `make sync-plans`.

## Pre-checks

- Read access to the production database (`docker compose -f
  docker-compose.prod.yml exec postgres psql`).
- Access to the Stripe dashboard for the account the deployment's
  `STRIPE_SECRET_KEY` belongs to. **Confirm test vs live mode** before
  reading anything: the two have separate subscriptions, and comparing one
  against the other invents drift that is not there.
- The backend is running a build that contains the duplicate-tolerant webhook
  handler. Before it, a second subscription on one org raised
  `UniqueViolation` on `ix_subscription_org_id`, so re-enabling a disabled
  endpoint with a duplicate queued behind it produced a permanent retry loop.
  Check with:

  ```bash
  docker compose -f docker-compose.prod.yml exec -T backend \
    python -c "from app.modules.billing.service import BillingService; \
      print(hasattr(BillingService, 'apply_subscription_snapshot'))"
  ```

  **Expected:** `True`. If it prints `False`, deploy first — do not re-enable
  the endpoint.

## Steps

### 1. Find out what the system thinks

```bash
docker compose -f docker-compose.prod.yml exec -T postgres \
  sh -c 'psql -U $POSTGRES_USER -d $POSTGRES_DB -x -f -' <<'SQL'
select id, type, received_at, processed_at, attempt_count, left(coalesce(error,'(none)'),200) as err
from webhook_event order by received_at desc limit 20;
SQL
```

**Expected:** every row has a `processed_at`. A row with `processed_at IS
NULL` and a non-empty `error` is an event that never applied; the `error`
text names the reason. An *empty* table when a customer has definitely paid
means no event was ever delivered — go to step 3.

Note that `error` is not cleared when a later retry succeeds: a row with both
an `error` and a `processed_at` recovered on its own.

### 2. Find out what Stripe thinks

In the Stripe dashboard, **Customers → the customer → Subscriptions**. Count
the ones that are Active, Trialing or Past due.

**Expected:** exactly one. Zero with a live local row, two or more, or one
whose status differs from the local row, are the three drift shapes this
runbook covers.

### 3. Check whether the endpoint is still enabled

**Developers → Webhooks → the endpoint.** Read its status and its list of
recent deliveries.

**Expected:** enabled, with recent deliveries succeeding. If it is disabled,
that alone explains total silence: events during that window were not
retried. Re-enable it — and only after the pre-check above passes.

While here, confirm the event selection contains all five types the backend
handles: `customer.subscription.created`, `customer.subscription.updated`,
`customer.subscription.deleted`, `invoice.payment_succeeded`,
`invoice.payment_failed`. A type that is not selected can never fire, and
nothing in this system can tell the difference between "not selected" and
"nothing happened".

### 4. Let reconciliation repair it

Nothing further is needed for a *single* subscription that is missing,
stale, or cancelled at Stripe: `reconcile_subscriptions` runs at 20 past
every hour, reads current state from the Stripe API, and applies it through
the same code path the webhook uses.

To not wait an hour:

```bash
docker compose -f docker-compose.prod.yml exec -T backend python -c "
import asyncio
from redis.asyncio import Redis
from app.core.config import get_settings
from app.workers.jobs.billing import reconcile_subscriptions
async def main():
    r = Redis.from_url(str(get_settings().REDIS_URL))
    await reconcile_subscriptions({'redis': r})
    await r.aclose()
asyncio.run(main())
"
```

**Expected:** a final log line `billing.reconcile_done: checked=N
repaired=M`. `repaired` counts orgs whose local state it changed.

### 5. Resolve a duplicate by hand

Reconciliation will **not** pick between two live subscriptions, and neither
should you do it in the database. It records
`billing.event_not_applied.duplicate_subscription` in `audit_event` and stops,
because choosing automatically can move the org onto whichever subscription is
about to be refunded.

In Stripe: cancel the duplicate (usually the newer one), and refund its
invoice from **Payments**. Then re-run step 4 — with one live subscription
left, the sweep applies it normally.

**Expected:** `select stripe_subscription_id, status from subscription where
org_id = '<org>'` names the surviving subscription, and the customer's plan
is correct in the app.

## Verification

From outside the procedure, not from its own output:

```bash
docker compose -f docker-compose.prod.yml exec -T postgres \
  sh -c 'psql -U $POSTGRES_USER -d $POSTGRES_DB -x -f -' <<'SQL'
select o.name, p.slug as plan, s.status, s.current_period_end
from subscription s
join plan p on p.id = s.plan_id
join organization o on o.id = s.org_id;
SQL
```

**Expected:** one row per paying org, each naming the plan and period Stripe
shows for that customer.

The negative check — the one that still looks fine if this did nothing: log
in as a user of the affected org and confirm the plan-gated feature actually
works. `subscription.status` being `active` is not the same as the org being
entitled; entitlement is computed in
`app/modules/billing/dependencies.py::_is_entitled`, which applies a grace
period to `past_due` and reads a cached row that a repair must invalidate.

## Rollback

Steps 1–3 are read-only and need no rollback.

Step 4 is idempotent: the sweep writes only where local state disagrees with
Stripe, so running it twice changes nothing the second time. If it applied
something you did not want, the fix is to correct Stripe and run it again —
never to edit `subscription` directly, which the next tick would simply
overwrite.

**Step 5 is the point of no return.** A refund cannot be undone, and a
cancelled subscription cannot be un-cancelled — a new one has to be created,
which charges the customer again. Confirm which of the two subscriptions the
customer should keep *before* cancelling either.

## Escalation

Stop and escalate when:

- **Reconciliation keeps repairing the same org every hour.** Local state is
  being overwritten by something else between ticks. Capture two consecutive
  `billing.reconcile_repair` log lines for that org before doing anything
  else; they name both the local and the remote state.
- **An event fails with the same error after a redeploy.** The handler cannot
  process that payload at all. Capture the row —
  `select payload from webhook_event where id = '<evt_...>'` — since it is
  the only copy; Stripe's own retention is limited, and the payload is what
  reproduces the bug in a test.
- **Stripe's own status page** (<https://status.stripe.com>) shows a
  webhooks or API incident. Reconciliation will catch up on its own once the
  incident clears; do not start cancelling subscriptions during one.

## Appendix: proving the failed-payment path

The `invoice.payment_failed` handler was broken from the day this account
moved to Stripe's Basil API version until 2026-09-12: it read
`invoice.subscription`, which Basil removed, so it took the `None` branch and
returned. No dunning campaign was ever started, and a customer whose card
failed would have stayed `active` and never been chased. Nothing detected it,
because a handler that returns early looks exactly like a handler with
nothing to do.

It is fixed and unit-tested, but no real failed payment has run through it in
production. This is how to prove it, and it is worth doing once:

1. In **test mode**, create a subscription for a throwaway customer using the
   card `4000 0000 0000 0341` — it attaches successfully and then fails on
   the first charge.
2. Watch the endpoint receive `invoice.payment_failed`.
3. Check the effects:

   ```bash
   docker compose -f docker-compose.prod.yml exec -T postgres \
     sh -c 'psql -U $POSTGRES_USER -d $POSTGRES_DB -x -f -' <<'SQL'
   select s.status, d.id as campaign, d.started_at
   from subscription s left join dunning_campaign d on d.subscription_id = s.id
   where s.stripe_subscription_id = '<sub_...>';
   SQL
   ```

   **Expected:** `status` is `past_due` and a `dunning_campaign` row exists.
   A `past_due` with no campaign means the status handler ran and the dunning
   start did not — check `audit_event` for `billing.payment_failed`.

Do this against the Stripe **test** account, never live: the point is to
produce a real failed charge, and doing that to a real customer to test a
code path is not a trade worth making.
