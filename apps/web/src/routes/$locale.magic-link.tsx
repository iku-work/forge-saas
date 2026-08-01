import { createFileRoute } from "@tanstack/react-router";
import { z } from "zod";

import { MagicLinkPage } from "../features/auth/MagicLinkPage";

const magicLinkSearchSchema = z.object({
  token: z.string().optional(),
});

export const Route = createFileRoute("/$locale/magic-link")({
  validateSearch: (search) => magicLinkSearchSchema.parse(search),
  component: MagicLinkRoute,
});

function MagicLinkRoute() {
  const { token } = Route.useSearch();
  return <MagicLinkPage token={token} />;
}
