import { createFileRoute } from "@tanstack/react-router";
import { z } from "zod";

import { ResetPasswordPage } from "../features/auth/ResetPasswordPage";
import { redirectIfAuthenticated } from "../lib/redirectIfAuthenticated";

const resetPasswordSearchSchema = z.object({
  token: z.string().optional(),
});

export const Route = createFileRoute("/$locale/reset-password")({
  validateSearch: (search) => resetPasswordSearchSchema.parse(search),
  // Only a bare visit redirects: the token may be for a *different*
  // account than the one signed in, and acting on it is legitimate.
  beforeLoad: ({ context, params, search }) => {
    redirectIfAuthenticated({ context, params, hasToken: Boolean(search.token) });
  },
  component: ResetPasswordRoute,
});

function ResetPasswordRoute() {
  const { token } = Route.useSearch();
  return <ResetPasswordPage token={token} />;
}
