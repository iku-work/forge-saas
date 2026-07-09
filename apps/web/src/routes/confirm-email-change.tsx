import { createFileRoute } from "@tanstack/react-router";
import { z } from "zod";

import { ConfirmEmailChangePage } from "../features/auth/ConfirmEmailChangePage";

const confirmEmailChangeSearchSchema = z.object({
  token: z.string().optional(),
});

export const Route = createFileRoute("/confirm-email-change")({
  validateSearch: (search) => confirmEmailChangeSearchSchema.parse(search),
  component: ConfirmEmailChangeRoute,
});

function ConfirmEmailChangeRoute() {
  const { token } = Route.useSearch();
  return <ConfirmEmailChangePage token={token} />;
}
