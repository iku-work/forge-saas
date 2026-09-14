import { createFileRoute } from "@tanstack/react-router";
import { z } from "zod";

import { MagicLinkPage } from "../features/auth/MagicLinkPage";
import { redirectIfAuthenticated } from "../lib/redirectIfAuthenticated";

const magicLinkSearchSchema = z.object({
  token: z.string().optional(),
});

export const Route = createFileRoute("/$locale/magic-link")({
  validateSearch: (search) => magicLinkSearchSchema.parse(search),
  // Only a bare visit redirects: the token may be for a *different*
  // account than the one signed in, and acting on it is legitimate.
  beforeLoad: ({ context, params, search }) => {
    redirectIfAuthenticated({ context, params, hasToken: Boolean(search.token) });
  },
  component: MagicLinkRoute,
});

function MagicLinkRoute() {
  const { token } = Route.useSearch();
  return <MagicLinkPage token={token} />;
}
