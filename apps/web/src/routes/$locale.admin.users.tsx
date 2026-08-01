import { createFileRoute } from "@tanstack/react-router";

import { AdminUsersPage } from "../features/admin/AdminUsersPage";

export const Route = createFileRoute("/$locale/admin/users")({
  component: AdminUsersPage,
});
