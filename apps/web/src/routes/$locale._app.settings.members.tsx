import { createFileRoute } from "@tanstack/react-router";

import { MembersSettingsPage } from "../features/settings/MembersSettingsPage";

export const Route = createFileRoute("/$locale/_app/settings/members")({
  component: MembersSettingsPage,
});
