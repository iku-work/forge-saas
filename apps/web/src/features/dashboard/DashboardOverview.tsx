import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import {
  DataListMuted,
  DataListRow,
  SettingsSection,
  StatusPill,
} from "@forge/ui";
import { formatBytes, formatDate } from "@forge/utils";

import {
  subscriptionStatusLabel,
  subscriptionTone,
} from "../../lib/statusLabels";
import { useRouteLocale } from "../../lib/useRouteLocale";
import type { DashboardOverviewData } from "./useGettingStarted";

/**
 * The dashboard once the Getting started checklist is done: a couple of
 * figures and the newest files, from the queries the checklist already ran
 * (so it costs no request of its own). Each part appears only when its data
 * does -- a module that is off, or a role that cannot see members or billing,
 * leaves it out rather than showing an empty box.
 */
export function DashboardOverview({
  overview,
}: {
  overview: DashboardOverviewData;
}) {
  const { t, i18n } = useTranslation();
  const locale = useRouteLocale();
  const { membersTotal, recentFiles, subscription } = overview;
  const hasFigures = membersTotal !== null || subscription !== null;

  if (!hasFigures && recentFiles.length === 0) {
    return null;
  }

  return (
    <div data-testid="dashboard-overview">
      {hasFigures ? (
        <dl className="grid grid-cols-1 gap-x-8 gap-y-6 border-t border-border pt-5 sm:grid-cols-3">
          {membersTotal !== null ? (
            <div>
              <dt className="text-[0.8125rem] text-muted-foreground">
                {t("common.dashboard.overview_members")}
              </dt>
              <dd className="mt-1">
                <Link
                  to="/$locale/settings/members"
                  params={{ locale }}
                  className="text-[1.75rem] font-semibold tabular-nums hover:underline"
                >
                  {membersTotal}
                </Link>
              </dd>
            </div>
          ) : null}
          {subscription !== null ? (
            <div>
              <dt className="text-[0.8125rem] text-muted-foreground">
                {t("common.dashboard.overview_plan")}
              </dt>
              <dd className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1">
                <StatusPill tone={subscriptionTone(subscription.status)}>
                  {subscriptionStatusLabel(t, subscription.status)}
                </StatusPill>
                {subscription.status === "active" ? (
                  <span className="text-[0.8125rem] text-muted-foreground">
                    {t("common.dashboard.renews", {
                      date: formatDate(
                        subscription.current_period_end,
                        i18n.language,
                      ),
                    })}
                  </span>
                ) : null}
              </dd>
            </div>
          ) : null}
        </dl>
      ) : null}

      {recentFiles.length > 0 ? (
        <SettingsSection
          title={t("common.dashboard.recent_files")}
          footer={
            <Link
              to="/$locale/files"
              params={{ locale }}
              className="text-[0.8125rem] font-medium text-primary hover:underline"
            >
              {t("common.dashboard.view_all_files")}
            </Link>
          }
        >
          <div className="border-t border-border">
            {recentFiles.map((file) => (
              <DataListRow
                key={file.id}
                className="grid-cols-[minmax(0,1fr)_auto_auto]"
              >
                <span className="truncate font-medium">{file.filename}</span>
                <DataListMuted>{formatBytes(file.size_bytes)}</DataListMuted>
                <DataListMuted>
                  {formatDate(file.created_at, i18n.language)}
                </DataListMuted>
              </DataListRow>
            ))}
          </div>
        </SettingsSection>
      ) : null}
    </div>
  );
}
