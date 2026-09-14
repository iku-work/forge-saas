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
interface Delivery {
  webhookSecret: string;
  /** Seconds since epoch that Stripe stamps on the *event*, not the object.
   *  Defaults to now; pass an older value to exercise the staleness guard. */
  createdAt?: number;
}

async function deliver(event: Record<string, unknown>, opts: Delivery): Promise<void> {
  const payload = Buffer.from(JSON.stringify(event), "utf-8");
  const timestamp = Math.floor(Date.now() / 1000);
  const signature = signStripePayload(payload, opts.webhookSecret, timestamp);

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

function eventId(): string {
  return `evt_e2e_${String(Date.now())}_${Math.random().toString(36).slice(2)}`;
}

/**
 * A Stripe Subscription object in the shape the API actually sends.
 *
 * `2025-03-31.basil` moved the billing period off the subscription and onto
 * its items, and this fixture used to carry the pre-Basil shape -- which is
 * how the real bug shipped: the suite asserted against an API Stripe no
 * longer has. A fixture that lies is worse than no fixture, so this one is
 * Basil-shaped and the legacy variant is a deliberate, named opt-in.
 */
function subscriptionObject(opts: {
  subscriptionId: string;
  orgId: string;
  stripePriceId: string;
  status: string;
  periodStart: number;
  periodEnd: number;
  cancelAt?: number | null;
  legacyShape?: boolean;
}): Record<string, unknown> {
  const item: Record<string, unknown> = { price: { id: opts.stripePriceId } };
  const base: Record<string, unknown> = {
    id: opts.subscriptionId,
    object: "subscription",
    status: opts.status,
    cancel_at_period_end: false,
    cancel_at: opts.cancelAt ?? null,
    trial_end: null,
    metadata: { org_id: opts.orgId },
  };
  if (opts.legacyShape) {
    base["current_period_start"] = opts.periodStart;
    base["current_period_end"] = opts.periodEnd;
  } else {
    item["current_period_start"] = opts.periodStart;
    item["current_period_end"] = opts.periodEnd;
  }
  base["items"] = { data: [item] };
  return base;
}

/**
 * An Invoice in Basil's shape: the subscription it belongs to lives under
 * `parent.subscription_details`, not the removed top-level `subscription`.
 * Reading the old field made both invoice handlers no-ops, so dunning never
 * started for anyone.
 */
function invoiceObject(opts: {
  invoiceId: string;
  subscriptionId: string;
  orgId: string;
}): Record<string, unknown> {
  return {
    id: opts.invoiceId,
    object: "invoice",
    parent: {
      type: "subscription_details",
      quote_details: null,
      subscription_details: {
        subscription: opts.subscriptionId,
        metadata: { org_id: opts.orgId },
      },
    },
  };
}

const THIRTY_DAYS = 30 * 24 * 60 * 60;

export interface SubscriptionEventOptions extends Delivery {
  orgId: string;
  stripePriceId: string;
  subscriptionId?: string;
  status?: string;
  /** Emit the pre-Basil shape instead, to prove the fallback still works. */
  legacyShape?: boolean;
}

// BillingService.handle_event has no handler at all for
// "checkout.session.completed" -- by design, a deliberate no-op placeholder
// there. The plan flip a journey observes is driven by
// "customer.subscription.created", exactly as real Stripe delivery would be
// (Checkout completing a subscription-mode session is what creates the
// underlying Subscription object in the first place).
export async function simulateSubscriptionEvent(
  type:
    | "customer.subscription.created"
    | "customer.subscription.updated"
    | "customer.subscription.deleted",
  opts: SubscriptionEventOptions,
): Promise<string> {
  const now = Math.floor(Date.now() / 1000);
  const subscriptionId = opts.subscriptionId ?? `sub_e2e_${String(now)}`;
  await deliver(
    {
      id: eventId(),
      object: "event",
      type,
      created: opts.createdAt ?? now,
      data: {
        object: subscriptionObject({
          subscriptionId,
          orgId: opts.orgId,
          stripePriceId: opts.stripePriceId,
          status: opts.status ?? "active",
          periodStart: now,
          periodEnd: now + THIRTY_DAYS,
          ...(opts.legacyShape === undefined ? {} : { legacyShape: opts.legacyShape }),
        }),
      },
    },
    opts,
  );
  return subscriptionId;
}

export async function simulateInvoiceEvent(
  type: "invoice.payment_succeeded" | "invoice.payment_failed",
  opts: Delivery & { orgId: string; subscriptionId: string; invoiceId?: string },
): Promise<void> {
  const now = Math.floor(Date.now() / 1000);
  await deliver(
    {
      id: eventId(),
      object: "event",
      type,
      created: opts.createdAt ?? now,
      data: {
        object: invoiceObject({
          invoiceId: opts.invoiceId ?? `in_e2e_${String(now)}`,
          subscriptionId: opts.subscriptionId,
          orgId: opts.orgId,
        }),
      },
    },
    opts,
  );
}

/** Back-compat for acceptance.spec.ts, which only ever needed the one event. */
export async function simulateSubscriptionCreated(opts: {
  webhookSecret: string;
  orgId: string;
  stripePriceId: string;
}): Promise<void> {
  await simulateSubscriptionEvent("customer.subscription.created", opts);
}
