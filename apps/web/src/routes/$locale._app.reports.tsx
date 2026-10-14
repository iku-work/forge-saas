import { createFileRoute } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { Page, PageHeader } from "@forge/ui";

export const Route = createFileRoute("/$locale/_app/reports")({
  component: ReportsPage,
});

// Placeholder destination for the "Advanced reports" nav item (T-121) --
// the nav link itself is what's gated by useFeatureFlag("advanced_reports");
// a real reports feature (queries, charts, exports) is a separate,
// not-yet-numbered future task.
function ReportsPage() {
  const { t } = useTranslation();
  return (
    <Page>
      <PageHeader
        title={t("common.nav.reports")}
        description={t("common.reports.description")}
      />
    </Page>
  );
}
