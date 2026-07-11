// Two upgrade strategies, matching T-220's spec:
//   - default (every PR): simulateSubscriptionCreated() below posts a real,
//     HMAC-signed Stripe event straight at the T-030 webhook endpoint,
//     bypassing Stripe Checkout entirely (backend/.env.test's
//     STRIPE_SECRET_KEY is a dummy value -- an actual Checkout Session
//     redirect is never reachable in CI).
//   - STRIPE_E2E=1 (nightly): not implemented here -- driving the real
//     Stripe-hosted Checkout page needs a real test-mode account/key this
//     repo does not have configured yet. acceptance.spec.ts skips that path
//     with a documented reason until those secrets exist (see its own
//     comment) rather than silently pretending to cover it.
import { createHmac } from "node:crypto";

import { request } from "@playwright/test";

import { BASE_URL } from "../config";

// Ports app.modules.billing.webhooks' _sign() (backend/tests/test_billing_webhooks.py)
// to Node -- see that file's docstring for the exact scheme this mirrors
// (Stripe's own documented "t=<ts>,v1=<hmac>" signature header format).
function signStripePayload(payload: Buffer, secret: string, timestamp: number): string {
  const signedPayload = `${String(timestamp)}.${payload.toString("utf-8")}`;
  const signature = createHmac("sha256", secret).update(signedPayload, "utf-8").digest("hex");
  return `t=${String(timestamp)},v1=${signature}`;
}

// BillingService.handle_event (backend/app/modules/billing/service.py) has
// no handler at all for "checkout.session.completed" -- by design, per that
// file's own test fixture comment, it's a deliberate no-op placeholder type
// there. The actual plan-flip this journey needs to observe is driven by
// "customer.subscription.created", exactly as real Stripe delivery would
// (Checkout completing a subscription-mode session is what creates the
// underlying Subscription object in the first place).
export async function simulateSubscriptionCreated(opts: {
  webhookSecret: string;
  orgId: string;
  stripePriceId: string;
}): Promise<void> {
  const now = Math.floor(Date.now() / 1000);
  const event = {
    id: `evt_e2e_${String(now)}_${Math.random().toString(36).slice(2)}`,
    object: "event",
    type: "customer.subscription.created",
    data: {
      object: {
        id: `sub_e2e_${String(now)}`,
        status: "active",
        current_period_start: now,
        current_period_end: now + 30 * 24 * 60 * 60,
        cancel_at_period_end: false,
        items: { data: [{ price: { id: opts.stripePriceId } }] },
        metadata: { org_id: opts.orgId },
      },
    },
  };
  const payload = Buffer.from(JSON.stringify(event), "utf-8");
  const signature = signStripePayload(payload, opts.webhookSecret, now);

  // A dedicated APIRequestContext rather than the browser's own `request`
  // fixture -- this call is server-to-server (no session cookie involved,
  // verified purely by the HMAC signature) and needs ignoreHTTPSErrors for
  // the dev Caddy cert the same way the browser context does.
  const api = await request.newContext({ baseURL: BASE_URL, ignoreHTTPSErrors: true });
  try {
    const response = await api.post("/api/v1/billing/webhooks/stripe", {
      headers: { "content-type": "application/json", "stripe-signature": signature },
      data: payload,
    });
    if (!response.ok()) {
      throw new Error(
        `stripe webhook stub failed: ${String(response.status())} ${await response.text()}`,
      );
    }
  } finally {
    await api.dispose();
  }
}
