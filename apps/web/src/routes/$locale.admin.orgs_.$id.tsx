import { createFileRoute } from "@tanstack/react-router";

import { AdminOrgDetailPage } from "../features/admin/AdminOrgDetailPage";

export const Route = createFileRoute("/$locale/admin/orgs_/$id")({
  component: AdminOrgDetailRoute,
});

function AdminOrgDetailRoute() {
  const { id } = Route.useParams();
  return <AdminOrgDetailPage orgId={id} />;
}
