import { createFileRoute } from "@tanstack/react-router";

import { AdminUserDetailPage } from "../features/admin/AdminUserDetailPage";

export const Route = createFileRoute("/$locale/admin/users_/$id")({
  component: AdminUserDetailRoute,
});

function AdminUserDetailRoute() {
  const { id } = Route.useParams();
  return <AdminUserDetailPage userId={id} />;
}
