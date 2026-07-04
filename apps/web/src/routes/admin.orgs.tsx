import { createFileRoute } from "@tanstack/react-router";

import { AdminOrgsPage } from "../features/admin/AdminOrgsPage";

export const Route = createFileRoute("/admin/orgs")({
  component: AdminOrgsPage,
});
