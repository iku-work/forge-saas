import { createFileRoute, notFound } from "@tanstack/react-router";

import { bootstrapModules, getModulesSnapshot } from "@forge/hooks";

import { BillingSettingsPage } from "../features/settings/BillingSettingsPage";

export const Route = createFileRoute("/_app/settings/billing")({
  beforeLoad: async () => {
    await bootstrapModules();
    if (!getModulesSnapshot().billing) {
      throw notFound();
    }
  },
  component: BillingSettingsPage,
});
