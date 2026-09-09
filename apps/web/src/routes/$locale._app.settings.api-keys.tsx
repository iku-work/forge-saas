import { createFileRoute, notFound } from "@tanstack/react-router";

import { bootstrapModules, getModulesSnapshot } from "@forge/hooks";

import { ApiKeysPage } from "../features/settings/ApiKeysPage";

export const Route = createFileRoute("/$locale/_app/settings/api-keys")({
  // T-313: the route disappears with the module, exactly like the nav entry
  // in AppLayout. A product with `public_api` off has no /api/v1/api-keys
  // routes at all, so a page that rendered would be a wall of 404s.
  beforeLoad: async () => {
    await bootstrapModules();
    if (!getModulesSnapshot().publicApi) {
      throw notFound();
    }
  },
  component: ApiKeysPage,
});
