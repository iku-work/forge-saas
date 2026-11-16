import { createFileRoute } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { Page, PageHeader } from "@forge/ui";

import { MyInvitationsList } from "../features/invitations/MyInvitationsList";

// Where an "invitation_received" notification leads: the orgs that have
// invited this person, to accept or decline in the app.
export const Route = createFileRoute("/$locale/_app/invitations")({
  component: InvitationsPage,
});

function InvitationsPage() {
  const { t } = useTranslation();
  return (
    <Page>
      <PageHeader
        title={t("tenancy.my_invitations.title")}
        description={t("tenancy.my_invitations.description")}
      />
      <MyInvitationsList />
    </Page>
  );
}
