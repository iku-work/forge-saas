import { createFileRoute } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

export const Route = createFileRoute("/_app/")({
  component: DashboardPage,
});

function DashboardPage() {
  const { t } = useTranslation();
  return (
    <div className="p-6">
      <h1 className="text-2xl font-semibold">{t("common.nav.dashboard")}</h1>
      <p className="mt-2 text-sm text-slate-400">
        {t("common.dashboard.empty")}
      </p>
    </div>
  );
}
