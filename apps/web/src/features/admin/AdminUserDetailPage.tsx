import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { getAdminUserDetail } from "@forge/api-client";
import {
  Alert,
  AlertDescription,
  Badge,
  Button,
  Card,
  CardContent,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@forge/ui";
import { formatDate } from "@forge/utils";

import { useRouteLocale } from "../../lib/useRouteLocale";
import { getErrorMessage } from "../auth/getErrorMessage";
import { ImpersonateModal } from "./ImpersonateModal";
import { MobileHandoffModal } from "./MobileHandoffModal";

export function AdminUserDetailPage({ userId }: { userId: string }) {
  const locale = useRouteLocale();
  const { t, i18n } = useTranslation();
  const [impersonateOpen, setImpersonateOpen] = useState(false);
  const [handoffOpen, setHandoffOpen] = useState(false);

  const detailQuery = useQuery({
    queryKey: ["admin", "users", "detail", userId],
    queryFn: async () => {
      const { data } = await getAdminUserDetail(userId);
      return data;
    },
  });
  const detail = detailQuery.data?.data ?? null;

  return (
    <div className="max-w-4xl space-y-6 p-6">
      <Link
        to="/$locale/admin/users"
        params={{ locale }}
        className="text-sm text-muted-foreground underline underline-offset-4"
      >
        {t("common.admin.user_detail.back_to_users")}
      </Link>

      {detailQuery.isLoading ? (
        <Skeleton className="h-10 w-64" />
      ) : detailQuery.isError ? (
        <Alert variant="destructive">
          <AlertDescription>
            {getErrorMessage(detailQuery.error, "common.admin.user_detail.error_load")}
          </AlertDescription>
        </Alert>
      ) : detail ? (
        <>
          <div className="flex items-center justify-between">
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-xl font-semibold">{detail.email}</h1>
                {detail.is_superadmin ? (
                  <Badge variant="destructive">
                    {t("common.admin.user_detail.superadmin_badge")}
                  </Badge>
                ) : null}
              </div>
              {detail.full_name ? (
                <p className="text-sm text-muted-foreground">{detail.full_name}</p>
              ) : null}
              <p className="text-sm text-muted-foreground">
                {t("common.admin.user_detail.created_line", {
                  date: formatDate(detail.created_at, i18n.language),
                })}
              </p>
            </div>
            {!detail.is_superadmin ? (
              <div className="flex gap-2">
                <Button
                  type="button"
                  onClick={() => {
                    setImpersonateOpen(true);
                  }}
                >
                  {t("common.admin.user_detail.impersonate_button")}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => {
                    setHandoffOpen(true);
                  }}
                >
                  {t("common.admin.user_detail.mobile_handoff_button")}
                </Button>
              </div>
            ) : null}
          </div>

          <Card>
            <CardContent className="space-y-2 pt-6">
              <h2 className="text-sm font-semibold">{t("common.admin.user_detail.memberships_title")}</h2>
              {detail.memberships.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  {t("common.admin.user_detail.no_memberships")}
                </p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t("common.admin.user_detail.col_org")}</TableHead>
                      <TableHead>{t("common.admin.user_detail.col_role")}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {detail.memberships.map((membership) => (
                      <TableRow key={membership.org_id}>
                        <TableCell>
                          <Link
                            to="/$locale/admin/orgs/$id"
                            params={{ locale, id: membership.org_id }}
                            className="underline underline-offset-4"
                          >
                            {membership.org_name}
                          </Link>
                        </TableCell>
                        <TableCell>
                          <Badge variant="secondary">{membership.role}</Badge>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardContent className="space-y-2 pt-6">
              <h2 className="text-sm font-semibold">{t("common.admin.user_detail.sessions_title")}</h2>
              {detail.sessions.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  {t("common.admin.user_detail.no_sessions")}
                </p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t("common.admin.user_detail.col_device")}</TableHead>
                      <TableHead>{t("common.admin.user_detail.col_last_seen")}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {detail.sessions.map((session) => (
                      <TableRow key={session.id}>
                        <TableCell>{session.device_label ?? "—"}</TableCell>
                        <TableCell>{formatDate(session.last_seen_at, i18n.language)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardContent className="space-y-2 pt-6">
              <h2 className="text-sm font-semibold">
                {t("common.admin.user_detail.audit_heading", {
                  count: detail.audit_summary.total_count,
                })}
              </h2>
              {detail.audit_summary.recent_actions.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  {t("common.admin.user_detail.no_audit")}
                </p>
              ) : (
                <div className="space-y-1">
                  {detail.audit_summary.recent_actions.map((entry, index) => (
                    <div
                      key={`${entry.action}-${String(index)}`}
                      className="flex items-center justify-between text-sm"
                    >
                      <span>{entry.action}</span>
                      <span className="text-muted-foreground">
                        {formatDate(entry.ts, i18n.language)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          <ImpersonateModal
            userId={detail.id}
            email={detail.email}
            open={impersonateOpen}
            onOpenChange={setImpersonateOpen}
          />
          <MobileHandoffModal
            userId={detail.id}
            email={detail.email}
            open={handoffOpen}
            onOpenChange={setHandoffOpen}
          />
        </>
      ) : null}
    </div>
  );
}
