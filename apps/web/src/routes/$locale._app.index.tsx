import { createFileRoute, Link } from "@tanstack/react-router";
import { LayoutDashboard } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useModules } from "@forge/hooks";
import { Button, EmptyState } from "@forge/ui";

import { useRouteLocale } from "../lib/useRouteLocale";

export const Route = createFileRoute("/$locale/_app/")({
  component: DashboardPage,
});

/**
 * The dashboard is the first authenticated screen a new account sees, which
 * makes its empty state the single highest-leverage one in the product
 * (F-010, and §1.5's halo effect). It used to read "Nothing here yet." --
 * true, and of no use to anyone.
 *
 * The primary action points at whichever surface the product actually has:
 * uploading a file if file storage is on, inviting a teammate otherwise.
 * Offering a link to a module that has been turned off would be worse than
 * offering nothing.
 */
function DashboardPage() {
  const { t } = useTranslation();
  const locale = useRouteLocale();
  const modules = useModules();

  return (
    <div className="p-6">
      <h1 className="text-2xl font-semibold" data-testid="page-title">
        {t("common.nav.dashboard")}
      </h1>
      <EmptyState
        className="py-16"
        icon={LayoutDashboard}
        data-testid="dashboard-empty"
        title={t("common.dashboard.empty_title")}
        description={t("common.dashboard.empty_body")}
        action={
          modules.fileStorage ? (
            <Button asChild>
              <Link to="/$locale/files" params={{ locale }}>
                {t("common.dashboard.empty_action_files")}
              </Link>
            </Button>
          ) : (
            <Button asChild>
              <Link to="/$locale/settings/members" params={{ locale }}>
                {t("common.dashboard.empty_action_invite")}
              </Link>
            </Button>
          )
        }
        secondaryAction={
          <Button asChild variant="outline">
            <Link to="/$locale/settings/profile" params={{ locale }}>
              {t("common.dashboard.empty_action_profile")}
            </Link>
          </Button>
        }
      />
    </div>
  );
}
