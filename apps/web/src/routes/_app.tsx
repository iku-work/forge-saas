import { createFileRoute, Outlet, redirect } from "@tanstack/react-router";

import { AppLayout } from "../layouts/AppLayout";

export const Route = createFileRoute("/_app")({
  beforeLoad: ({ context }) => {
    if (!context.auth.user) {
      throw redirect({ to: "/login" });
    }
    if (!context.auth.user.email_verified_at) {
      throw redirect({ to: "/verify-email" });
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
