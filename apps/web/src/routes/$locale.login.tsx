import { createFileRoute } from "@tanstack/react-router";

import { LoginPage } from "../features/auth/LoginPage";
import { redirectIfAuthenticated } from "../lib/redirectIfAuthenticated";

export const Route = createFileRoute("/$locale/login")({
  beforeLoad: redirectIfAuthenticated,
  component: LoginPage,
});
