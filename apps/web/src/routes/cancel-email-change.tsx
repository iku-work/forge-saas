import { createFileRoute } from "@tanstack/react-router";
import { z } from "zod";

import { CancelEmailChangePage } from "../features/auth/CancelEmailChangePage";

const cancelEmailChangeSearchSchema = z.object({
  token: z.string().optional(),
});

export const Route = createFileRoute("/cancel-email-change")({
  validateSearch: (search) => cancelEmailChangeSearchSchema.parse(search),
  component: CancelEmailChangeRoute,
});

function CancelEmailChangeRoute() {
  const { token } = Route.useSearch();
  return <CancelEmailChangePage token={token} />;
}
