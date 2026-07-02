import { createFileRoute } from "@tanstack/react-router";

import { AcceptInvitationPage } from "../features/auth/AcceptInvitationPage";

export const Route = createFileRoute("/accept-invitation/$token")({
  component: AcceptInvitationRoute,
});

function AcceptInvitationRoute() {
  const { token } = Route.useParams();
  return <AcceptInvitationPage token={token} />;
}
