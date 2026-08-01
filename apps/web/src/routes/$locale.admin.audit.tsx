import { createFileRoute } from "@tanstack/react-router";

import { AdminAuditPage } from "../features/admin/AdminAuditPage";

export const Route = createFileRoute("/$locale/admin/audit")({
  component: AdminAuditPage,
});
