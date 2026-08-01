import { createFileRoute } from "@tanstack/react-router";

import { ForgotPasswordPage } from "../features/auth/ForgotPasswordPage";

export const Route = createFileRoute("/$locale/forgot-password")({
  component: ForgotPasswordPage,
});
