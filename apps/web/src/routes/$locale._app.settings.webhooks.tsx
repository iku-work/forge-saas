import { createFileRoute, notFound } from "@tanstack/react-router";

import { bootstrapModules, getModulesSnapshot } from "@forge/hooks";

import { WebhooksPage } from "../features/settings/WebhooksPage";

export const Route = createFileRoute("/$locale/_app/settings/webhooks")({
  // T-322, same shape as the API-keys route beside it.
  beforeLoad: async () => {
    await bootstrapModules();
    if (!getModulesSnapshot().outboundWebhooks) {
      throw notFound();
    }
  },
  component: WebhooksPage,
});
