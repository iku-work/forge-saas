import { createFileRoute } from "@tanstack/react-router";

import { ProfileSettingsPage } from "../features/settings/ProfileSettingsPage";

export const Route = createFileRoute("/$locale/_app/settings/profile")({
  component: ProfileSettingsPage,
});
