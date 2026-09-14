import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { getAdminDashboard } from "@forge/api-client";
import {
  Alert,
  AlertDescription,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  Skeleton,
} from "@forge/ui";
import { formatDate } from "@forge/utils";

import { useRouteLocale } from "../../lib/useRouteLocale";
import { getErrorMessage } from "../auth/getErrorMessage";

function formatMrr(cents: number, locale: string): string {
  return new Intl.NumberFormat(locale, { style: "currency", currency: "USD" }).format(
    cents / 100,
  );
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

  return (
    <div className="max-w-5xl space-y-6 p-6">
      <h1 className="text-xl font-semibold" data-testid="page-title">{t("common.admin.dashboard.title")}</h1>

      {dashboardQuery.isLoading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : dashboardQuery.isError ? (
        <Alert variant="destructive">
          <AlertDescription>
            {getErrorMessage(dashboardQuery.error, "common.admin.dashboard.error_load")}
          </AlertDescription>
        </Alert>
      ) : stats ? (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <Card>
              <CardHeader>
                <CardDescription>{t("common.admin.dashboard.org_count")}</CardDescription>
              </CardHeader>
              <CardContent>
                <p className="text-3xl font-semibold">{stats.org_count}</p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardDescription>{t("common.admin.dashboard.user_count")}</CardDescription>
              </CardHeader>
              <CardContent>
                <p className="text-3xl font-semibold">{stats.user_count}</p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardDescription>{t("common.admin.dashboard.mrr")}</CardDescription>
              </CardHeader>
              <CardContent>
                <p className="text-3xl font-semibold">
                  {stats.mrr_cents === null
                    ? t("common.admin.dashboard.mrr_unavailable")
                    : formatMrr(stats.mrr_cents, i18n.language)}
                </p>
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardDescription>{t("common.admin.dashboard.recent_signups")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              {stats.recent_signups.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  {t("common.admin.dashboard.no_signups")}
                </p>
              ) : (
                stats.recent_signups.map((signup) => (
                  <div
                    key={signup.id}
                    className="flex items-center justify-between text-sm"
                  >
                    <Link
                      to="/$locale/admin/users/$id"
                      params={{ locale, id: signup.id }}
                      className="underline underline-offset-4"
                    >
                      {signup.email}
                    </Link>
                    <span className="text-muted-foreground">
                      {formatDate(signup.created_at, i18n.language)}
                    </span>
                  </div>
                ))
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardDescription>
                {t("common.admin.dashboard.recent_payment_failures")}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              {stats.recent_payment_failures.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  {t("common.admin.dashboard.no_payment_failures")}
                </p>
              ) : (
                stats.recent_payment_failures.map((failure, index) => (
                  <div
                    key={`${failure.org_id}-${String(index)}`}
                    className="flex items-center justify-between text-sm"
                  >
                    <Link
                      to="/$locale/admin/orgs/$id"
                      params={{ locale, id: failure.org_id }}
                      className="underline underline-offset-4"
                    >
                      {failure.org_name}
                    </Link>
                    <span className="text-muted-foreground">
                      {formatDate(failure.ts, i18n.language)}
                    </span>
                  </div>
                ))
              )}
            </CardContent>
          </Card>
        </>
      ) : null}
    </div>
  );
}
