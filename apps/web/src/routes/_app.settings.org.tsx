import { createFileRoute } from "@tanstack/react-router";

import { OrgSettingsPage } from "../features/settings/OrgSettingsPage";

export const Route = createFileRoute("/_app/settings/org")({
  component: OrgSettingsPage,
});
