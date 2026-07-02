import { createFileRoute, notFound } from "@tanstack/react-router";

import { bootstrapModules, getModulesSnapshot } from "@forge/hooks";

import { NotificationsSettingsPage } from "../features/settings/NotificationsSettingsPage";

export const Route = createFileRoute("/_app/settings/notifications")({
  beforeLoad: async () => {
    await bootstrapModules();
    if (!getModulesSnapshot().notifications) {
      throw notFound();
    }
  },
  component: NotificationsSettingsPage,
});
