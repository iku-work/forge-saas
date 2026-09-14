import { createFileRoute } from "@tanstack/react-router";

import { ForgotPasswordPage } from "../features/auth/ForgotPasswordPage";
import { redirectIfAuthenticated } from "../lib/redirectIfAuthenticated";

export const Route = createFileRoute("/$locale/forgot-password")({
  // No token to act on here, so a signed-in visitor has nothing to do on
  // this page -- changing a known password belongs in settings/security.
  beforeLoad: redirectIfAuthenticated,
  component: ForgotPasswordPage,
});
