import { createFileRoute, Outlet, redirect } from "@tanstack/react-router";

import { AppLayout } from "../layouts/AppLayout";

export const Route = createFileRoute("/$locale/_app")({
  // T-233: auth redirects keep the locale the visitor arrived in, so being
  // bounced to the login page never silently switches their language.
  beforeLoad: ({ context, params }) => {
    if (!context.auth.user) {
      throw redirect({ to: "/$locale/login", params });
    }
    if (!context.auth.user.email_verified_at) {
      throw redirect({ to: "/$locale/verify-email", params });
    }
  },
  component: AppShell,
});

function AppShell() {
  return (
    <AppLayout>
      <Outlet />
    </AppLayout>
  );
}
