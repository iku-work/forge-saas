import { createFileRoute } from "@tanstack/react-router";

import { AdminFeedbackPage } from "../features/admin/AdminFeedbackPage";

export const Route = createFileRoute("/$locale/admin/feedback")({
  component: AdminFeedbackPage,
});
