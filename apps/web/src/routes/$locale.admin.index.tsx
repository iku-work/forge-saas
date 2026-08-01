import { createFileRoute } from "@tanstack/react-router";

import { AdminDashboardPage } from "../features/admin/AdminDashboardPage";

export const Route = createFileRoute("/$locale/admin/")({
  component: AdminDashboardPage,
});
