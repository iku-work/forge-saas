import { createFileRoute } from "@tanstack/react-router";
import { z } from "zod";

import { ResetPasswordPage } from "../features/auth/ResetPasswordPage";

const resetPasswordSearchSchema = z.object({
  token: z.string().optional(),
});

export const Route = createFileRoute("/reset-password")({
  validateSearch: (search) => resetPasswordSearchSchema.parse(search),
  component: ResetPasswordRoute,
});

function ResetPasswordRoute() {
  const { token } = Route.useSearch();
  return <ResetPasswordPage token={token} />;
}
