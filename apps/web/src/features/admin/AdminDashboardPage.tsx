import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { getAdminDashboard } from "@forge/api-client";
import {
  Alert,
  AlertDescription,
  DataListMuted,
  DataListRow,
  Page,
  PageHeader,
  SettingsSection,
  Skeleton,
} from "@forge/ui";
import { formatDate } from "@forge/utils";

import { useRouteLocale } from "../../lib/useRouteLocale";
import { getErrorMessage } from "../auth/getErrorMessage";

function formatMrr(cents: number, locale: string): string {
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency: "USD",
  }).format(cents / 100);
}

export function AdminDashboardPage() {
  const locale = useRouteLocale();
  const { t, i18n } = useTranslation();

  const dashboardQuery = useQuery({
    queryKey: ["admin", "dashboard"],
    queryFn: async () => {
      const { data } = await getAdminDashboard();
      return data;
    },
  });

  const stats = dashboardQuery.data?.data ?? null;

  // Two columns: the link, then the date.
  const rowClass = "grid-cols-[minmax(0,1fr)_auto]";

  return (
    <Page>
      <PageHeader title={t("common.admin.dashboard.title")} />

      {dashboardQuery.isLoading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : dashboardQuery.isError ? (
        <Alert variant="destructive">
          <AlertDescription>
            {getErrorMessage(
              dashboardQuery.error,
              "common.admin.dashboard.error_load",
            )}
          </AlertDescription>
        </Alert>
      ) : stats ? (
        <>
          {/* Figures, not cards: three numbers in a row with a hairline
              above, read left to right like a statement. */}
          <dl className="grid grid-cols-1 gap-x-8 gap-y-6 border-t border-border pt-5 sm:grid-cols-3">
            <div>
              <dt className="text-[0.8125rem] text-muted-foreground">
                {t("common.admin.dashboard.org_count")}
              </dt>
              <dd className="mt-1 text-[1.75rem] font-semibold tabular-nums">
                {stats.org_count}
              </dd>
            </div>
            <div>
              <dt className="text-[0.8125rem] text-muted-foreground">
                {t("common.admin.dashboard.user_count")}
              </dt>
              <dd className="mt-1 text-[1.75rem] font-semibold tabular-nums">
                {stats.user_count}
              </dd>
            </div>
            <div>
              <dt className="text-[0.8125rem] text-muted-foreground">
                {t("common.admin.dashboard.mrr")}
              </dt>
              <dd className="mt-1 text-[1.75rem] font-semibold tabular-nums">
                {stats.mrr_cents === null
                  ? t("common.admin.dashboard.mrr_unavailable")
                  : formatMrr(stats.mrr_cents, i18n.language)}
              </dd>
            </div>
          </dl>

          <SettingsSection title={t("common.admin.dashboard.recent_signups")}>
            {stats.recent_signups.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                {t("common.admin.dashboard.no_signups")}
              </p>
            ) : (
              <div className="border-t border-border">
                {stats.recent_signups.map((signup) => (
                  <DataListRow key={signup.id} className={rowClass}>
                    <Link
                      to="/$locale/admin/users/$id"
                      params={{ locale, id: signup.id }}
                      className="truncate font-medium hover:underline"
                    >
                      {signup.email}
                    </Link>
                    <DataListMuted>
                      {formatDate(signup.created_at, i18n.language)}
                    </DataListMuted>
                  </DataListRow>
                ))}
              </div>
            )}
          </SettingsSection>

          <SettingsSection
            title={t("common.admin.dashboard.recent_payment_failures")}
          >
            {stats.recent_payment_failures.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                {t("common.admin.dashboard.no_payment_failures")}
              </p>
            ) : (
              <div className="border-t border-border">
                {stats.recent_payment_failures.map((failure, index) => (
                  <DataListRow
                    key={`${failure.org_id}-${String(index)}`}
                    className={rowClass}
                  >
                    <Link
                      to="/$locale/admin/orgs/$id"
                      params={{ locale, id: failure.org_id }}
                      className="truncate font-medium hover:underline"
                    >
                      {failure.org_name}
                    </Link>
                    <DataListMuted>
                      {formatDate(failure.ts, i18n.language)}
                    </DataListMuted>
                  </DataListRow>
                ))}
              </div>
            )}
          </SettingsSection>
        </>
      ) : null}
    </Page>
  );
}
