import { createFileRoute } from "@tanstack/react-router";
import { z } from "zod";

import { LoginPage } from "../features/auth/LoginPage";
import { redirectIfAuthenticated } from "../lib/redirectIfAuthenticated";

// `.catch`: the router parses search values as JSON, so `?sign_in_error=1` is a
// number. A bad value in a link must not put an error page in front of a login.
const loginSearchSchema = z.object({
  sign_in_error: z.string().optional().catch(undefined),
});

export const Route = createFileRoute("/$locale/login")({
  validateSearch: (search) => loginSearchSchema.parse(search),
  beforeLoad: redirectIfAuthenticated,
  component: LoginRoute,
});

function LoginRoute() {
  const { sign_in_error: signInError } = Route.useSearch();
  return <LoginPage signInError={signInError} />;
}
