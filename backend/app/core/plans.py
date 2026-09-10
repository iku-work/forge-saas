"""Plan definitions: what the product grants, keyed to what Stripe charges.

A Plan row needs a Stripe price id in `stripe_price_id_monthly` /
`stripe_price_id_yearly`, and that id is load-bearing in both directions --
BillingService.create_checkout_session sends it to Stripe, and
_handle_subscription_upserted resolves the incoming subscription *back* to a
Plan with PlanRepository.get_by_stripe_price_id. Get it wrong and checkout
fails loudly, while the webhook fails silently: it logs
`billing.webhook_unknown_price` and returns, leaving a customer who has paid
sitting on the free tier.

Those ids are opaque (`price_1SClowIpS6eDiRbaAlrS8r6g`) and differ between
Stripe's test and live modes, so copying them into a config file by hand is
both tedious and a standing invitation to that silent failure. It is also
unnecessary: Stripe prices carry a `lookup_key`, a stable name *you* choose,
and `Price.list(lookup_keys=[...])` resolves it. A price's amount is
immutable, but `transfer_lookup_key=True` moves the key to its replacement,
so the key outlives repricing.

So the split is:

    this file + product/config.py   what a plan GRANTS (entitlements), and
                                    the lookup key naming its price
    Stripe                          what it COSTS, and what it is called
    app.core.sync_plans             resolves one to the other, writing the
                                    ids nobody should be typing

Entitlements cannot come from Stripe -- Stripe has no idea what
`storage_gb: 50` means -- which is why this half stays in the repo.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PlanDefinition:
    """One purchasable plan. See app.core.sync_plans for how it is applied."""

    slug: str
    """Stable local identifier, the one the API takes (`plan_slug`). Never
    derived from Stripe -- renaming a product in the dashboard must not
    change what a client sends."""

    lookup_key_monthly: str
    """`lookup_key` of the monthly Stripe Price. Set it on the price in the
    dashboard (Product -> price -> Advanced) or via the API."""

    lookup_key_yearly: str | None = None
    """Optional: plenty of products bill monthly only. A plan without one
    simply has no yearly price id, and BillingService rejects an attempt to
    check out yearly with a missing price rather than inventing one."""

    features: dict[str, int] = field(default_factory=dict)
    """Entitlements, matching Plan.features. Read by require_feature and by
    the per-plan quota checks (see StorageService._check_quota). A key that
    is absent is a limit nobody enforces -- absent means unlimited, not
    zero."""

    usage_meter_event_names: dict[str, str] = field(default_factory=dict)
    """Metered dimension -> Stripe Billing Meter `event_name`, matching
    Plan.usage_meter_event_names. Empty for a plan that is not metered."""
