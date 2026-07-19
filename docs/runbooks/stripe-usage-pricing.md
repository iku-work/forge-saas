# Stripe usage-based pricing setup

Usage metering (`backend/app/modules/billing/usage_service.py`,
`app/workers/jobs/usage.py`) only ever counts and reports raw integer
`quantity` to Stripe — it never computes a price locally. The actual $/unit
rate for a metered dimension lives entirely on Stripe's side, in the Price
object attached to the metered Billing Meter. This runbook covers setting
that Price up with **sub-cent per-unit pricing** (e.g. $0.0001/API call,
where a plain 1-cent unit price would be 100x too coarse), and how
`Plan.usage_prices_micros` mirrors it locally for display purposes only.

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

## Setting it up

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
