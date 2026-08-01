import { createFileRoute, Outlet, redirect } from "@tanstack/react-router";

import { AdminLayout } from "../layouts/AdminLayout";

// T-143: a separate route tree from `_app` (guard, layout, and nav are all
// distinct) so support staff always know they're on the admin surface, not
// the ordinary user-facing app. Reachable at a real "/admin" URL -- unlike
// `_app`'s pathless layout -- so it's directly bookmarkable/linkable.
export const Route = createFileRoute("/$locale/admin")({
  beforeLoad: ({ context, params }) => {
    if (!context.auth.user) {
      throw redirect({ to: "/$locale/login", params });
    }
    if (!context.auth.user.is_superadmin) {
      throw redirect({ to: "/$locale", params });
    }
  },
  component: AdminShell,
});

function AdminShell() {
  return (
    <AdminLayout>
      <Outlet />
    </AdminLayout>
  );
}
