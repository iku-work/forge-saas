import { createFileRoute } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

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
    <div className="p-6">
      <h1 className="text-2xl font-semibold">{t("common.nav.reports")}</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        {t("common.reports.description")}
      </p>
    </div>
  );
}
