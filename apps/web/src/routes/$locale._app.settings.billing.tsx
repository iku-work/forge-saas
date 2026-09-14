import { createFileRoute, notFound } from "@tanstack/react-router";
import { z } from "zod";

import { bootstrapModules, getModulesSnapshot } from "@forge/hooks";

import { BillingSettingsPage } from "../features/settings/BillingSettingsPage";

// Where Stripe Checkout sends the customer back to. `success` is its
// success_url, `canceled` its cancel_url -- both set in
// app/modules/billing/service.py's _create_checkout_session_stripe.
//
// `catch` rather than a bare parse: this is a URL a customer can edit, and a
// nonsense value should render the page as if they had arrived normally, not
// throw them a router error on the page they came to pay on.
const billingSearchSchema = z.object({
  checkout: z.enum(["success", "canceled"]).optional().catch(undefined),
});

export const Route = createFileRoute("/$locale/_app/settings/billing")({
  validateSearch: (search) => billingSearchSchema.parse(search),
  beforeLoad: async () => {
    await bootstrapModules();
    if (!getModulesSnapshot().billing) {
      throw notFound();
    }
  },
  component: BillingSettingsRoute,
});

// A wrapper so BillingSettingsPage stays a plain component taking props --
// its tests render it directly, with no router around it (same shape as
// $locale.cancel-email-change.tsx).
function BillingSettingsRoute() {
  const { checkout } = Route.useSearch();
  return <BillingSettingsPage checkout={checkout} />;
}
