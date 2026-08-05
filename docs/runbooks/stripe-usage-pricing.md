# Stripe usage-based pricing setup

Usage metering (`backend/app/modules/billing/usage_service.py`,
`app/workers/jobs/usage.py`) only ever counts and reports raw integer
`quantity` to Stripe — it never computes a price locally. The actual $/unit
rate for a metered dimension lives entirely on Stripe's side, in the Price
object attached to the metered Billing Meter. This runbook covers setting
that Price up with **sub-cent per-unit pricing** (e.g. $0.0001/API call,
where a plain 1-cent unit price would be 100x too coarse), and how
`Plan.usage_prices_micros` mirrors it locally for display purposes only.

## Trigger

- A plan needs a metered dimension priced below one cent per unit — per API
  call, per email, per row processed.
- An existing metered rate changes, and `Plan.usage_prices_micros` has to
  follow it (nothing enforces that the two agree; see the last section).

Not this runbook: flat subscription pricing, which uses integer-cent
`unit_amount` and needs none of this, or *counting* usage, which is
`UsageService` and already works regardless of how the meter is priced.

## Pre-checks

- Stripe test mode available, and the intent to use it. Decimal-cent
  arithmetic is easy to get an order of magnitude wrong and the failure is
  silent until an invoice is generated — step 2's warning is the whole reason
  this document exists.
- The metric already reported to Stripe: an `event_name` in
  `Plan.usage_meter_event_names`, and `report_usage_to_stripe` running. Pricing
  a meter that nothing writes to produces a $0 invoice line, not an error.
- Agreement on the rate itself, written down in the currency's major unit
  ("$0.0001 per call"), not in cents. Every conversion below starts from that
  number, and half the mistakes here are made before Stripe is opened.

## Why `unit_amount_decimal`, not `unit_amount`

A Stripe `Price` has two mutually-exclusive amount fields:

- `unit_amount` — integer cents. What every *other* price in this app uses
  (`Plan.stripe_price_id_monthly`/`yearly`, both flat subscription prices).
- `unit_amount_decimal` — a decimal **string**, up to 12 decimal places past
  the major currency unit. This is Stripe's documented mechanism for
  metered/graduated pricing where the per-unit cost is a fraction of a
  cent — exactly this case.

Stripe is authoritative for what actually gets invoiced. Nothing in this
repo needs to change to use `unit_amount_decimal` — it's a Stripe Dashboard
(or `stripe.Price.create`) configuration choice, not a code path this app
controls.

## Steps

1. In the Stripe Dashboard (or via the API), create a **Billing Meter** for
   the metric, e.g. `api_calls_meter` — this is the `event_name` half of
   `Plan.usage_meter_event_names` (`backend/app/modules/billing/models.py`),
   e.g. `{"api_calls": "api_calls_meter"}`.
2. Create a metered **Price** on the product, attached to that meter, with
   **"Use decimal pricing"** enabled in the Dashboard (or
   `unit_amount_decimal="0.01"` via the API for $0.0001/unit — note the
   string is in the currency's *major* unit with decimals, e.g. `"0.01"`
   means $0.0001 since it's hundredths-of-a-cent... concretely: for a
   $0.0001/call rate, pass `unit_amount_decimal="0.01"` — Stripe interprets
   this as 0.01 cents, i.e. $0.0001). Double-check the resulting invoice
   line item in Stripe's test mode before shipping a rate — decimal-cents
   math is easy to get an order of magnitude wrong.
3. Attach that Price to the product's subscription (as an additional
   subscription item alongside the flat monthly/yearly price), or however
   your Stripe product structure organizes metered add-ons.
4. `app/workers/jobs/usage.py`'s existing `report_usage_to_stripe` job keeps
   working unmodified — it only ever reports raw `quantity` via
   `stripe.billing.MeterEvent.create`; Stripe applies whatever Price is
   attached to the meter (decimal or not) at invoicing time.

## `Plan.usage_prices_micros` — local mirror for estimates

The app has no way to show a user "$X so far this billing period" without
either calling Stripe on every page load or knowing the price locally. To
avoid the former, `Plan.usage_prices_micros: dict[str, int]`
(`billing/models.py`) stores the *same* per-unit rate as a plain integer, in
**micros** — 1,000,000 micros = $1.00, so $0.0001/call is `100`. This
follows the same integer-fixed-point convention Google's Ads/Analytics APIs
use ("micros"), not a `Decimal` column: it needs no new column type, stays
consistent with every other money field in this codebase being a plain
`int`, and 6 decimal digits of precision is comfortably enough for
per-call-style pricing.

```python
usage_prices_micros = {"api_calls": 100}  # $0.0001/call
```

`GET /api/v1/billing/usage` (`BillingService.get_usage_summary`) combines
this with the org's actual usage for the current billing period
(`UsageService.aggregate`, scoped to `Subscription.current_period_start/
end`) to return a per-metric `estimated_cost_micros` — exact integer
multiplication, no floating-point rounding.

**This value is an estimate only.** Keep it in sync with the Stripe Price's
`unit_amount_decimal` by hand — nothing enforces the two matching, the same
"keep in sync manually" caveat `packages/i18n/README.md` already documents
for `SUPPORTED_LOCALES` vs. product config. If a metric has an entry in
`usage_meter_event_names` but none in `usage_prices_micros`, the summary
endpoint reports `unit_price_micros: null` / `estimated_cost_micros: null`
for it rather than guessing — a metric can be tracked without being priced
(e.g. an included-quota dimension).

## Verification

In Stripe **test mode**, before this rate goes anywhere near a real customer:

1. Report a known quantity through the normal path (let
   `report_usage_to_stripe` run, or send a `MeterEvent` by hand) — pick a
   round number that makes the arithmetic obvious, like 10,000 units.
2. Look at the **draft invoice line item**, not the Price object. The Price
   says `unit_amount_decimal: "0.01"`; only the invoice says whether that
   meant $1.00 or $0.01 for 10,000 units. This is the check the whole runbook
   exists for.
3. Call `GET /api/v1/billing/usage` for the same org and confirm
   `estimated_cost_micros` agrees with the invoice line to the cent. A
   mismatch means `usage_prices_micros` and the Stripe Price have diverged —
   the failure mode that has nothing enforcing it.

The negative check: a metric that is metered but *not* priced must come back
as `unit_price_micros: null`, not `0`. A silent zero looks like a free tier
and reads as one right up until someone notices the revenue.

## Rollback

The local half reverts like any code change: drop or correct the
`usage_prices_micros` entry and redeploy. It only affects the estimate shown
in the app.

The Stripe half does not revert. **Prices are immutable** — `unit_amount_decimal`
cannot be edited once the Price exists. Undoing a wrong rate means creating a
new Price at the correct rate, moving the subscription item to it, and
archiving the old one. Existing invoices are unaffected: usage already
recorded against the old Price stays priced at the old rate, which is normally
what you want and is occasionally the problem.

**The point of no return is the first invoice finalized** against the wrong
rate. After that it is a billing correction — a credit note or a refund — not
a configuration change, and it belongs to whoever owns customer billing.

## Escalation

- **The invoice line doesn't match the intended rate** and the Price is
  already attached to a live subscription: stop reporting usage for that
  metric if you can, and treat it as a billing incident. Every hour of
  reported usage is another line at the wrong rate.
- **`report_usage_to_stripe` fails after the Price change.** Usually the meter
  and the Price are attached to different products, or the `event_name` in
  `usage_meter_event_names` no longer matches the meter's. The job's failures
  go through the Stripe circuit breaker — `degraded-mode.md` for whether the
  cause is us or them.
- **Currency without minor units** (JPY and friends): the "0.01 means
  hundredths of a cent" reasoning above is denominated in *cents* and does not
  transfer. Re-derive the conversion against Stripe's documentation for that
  currency rather than adapting the example here.
