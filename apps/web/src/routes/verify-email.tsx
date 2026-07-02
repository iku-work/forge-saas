import { createFileRoute } from "@tanstack/react-router";
import { z } from "zod";

import { VerifyEmailPage } from "../features/auth/VerifyEmailPage";

const verifyEmailSearchSchema = z.object({
  token: z.string().optional(),
});

export const Route = createFileRoute("/verify-email")({
  validateSearch: (search) => verifyEmailSearchSchema.parse(search),
  component: VerifyEmailRoute,
});

function VerifyEmailRoute() {
  const { token } = Route.useSearch();
  return <VerifyEmailPage token={token} />;
}
