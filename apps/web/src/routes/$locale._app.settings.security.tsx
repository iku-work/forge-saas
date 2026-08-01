import { createFileRoute } from "@tanstack/react-router";

import { SecuritySettingsPage } from "../features/settings/SecuritySettingsPage";

export const Route = createFileRoute("/$locale/_app/settings/security")({
  component: SecuritySettingsPage,
});
