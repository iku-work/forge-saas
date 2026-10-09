import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import { useTranslation } from "react-i18next";

import { getAdminUserDetail } from "@forge/api-client";
import {
  Alert,
  AlertDescription,
  Button,
  DataListMuted,
  DataListRow,
  Page,
  PageHeader,
  SettingsSection,
  Skeleton,
  StatusPill,
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
    <Page>
      <Link
        to="/$locale/admin/users"
        params={{ locale }}
        className="mb-3 inline-flex items-center gap-1.5 text-[0.8125rem] text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" aria-hidden="true" />
        {t("common.admin.user_detail.back_to_users")}
      </Link>

      {detailQuery.isLoading ? (
        <Skeleton className="h-10 w-64" />
      ) : detailQuery.isError ? (
        <Alert variant="destructive">
          <AlertDescription>
            {getErrorMessage(
              detailQuery.error,
              "common.admin.user_detail.error_load",
            )}
          </AlertDescription>
        </Alert>
      ) : detail ? (
        <>
          <PageHeader
            title={<span className="break-all">{detail.email}</span>}
            description={
              <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
                {detail.is_superadmin ? (
                  <StatusPill tone="danger">
                    {t("common.admin.user_detail.superadmin_badge")}
                  </StatusPill>
                ) : null}
                {detail.full_name ? <span>{detail.full_name}</span> : null}
                <span>
                  {t("common.admin.user_detail.created_line", {
                    date: formatDate(detail.created_at, i18n.language),
                  })}
                </span>
              </span>
            }
            actions={
              !detail.is_superadmin ? (
                <>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => {
                      setHandoffOpen(true);
                    }}
                  >
                    {t("common.admin.user_detail.mobile_handoff_button")}
                  </Button>
                  <Button
                    type="button"
                    onClick={() => {
                      setImpersonateOpen(true);
                    }}
                  >
                    {t("common.admin.user_detail.impersonate_button")}
                  </Button>
                </>
              ) : null
            }
          />

          <SettingsSection
            title={t("common.admin.user_detail.memberships_title")}
          >
            {detail.memberships.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                {t("common.admin.user_detail.no_memberships")}
              </p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>
                      {t("common.admin.user_detail.col_org")}
                    </TableHead>
                    <TableHead>
                      {t("common.admin.user_detail.col_role")}
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {detail.memberships.map((membership) => (
                    <TableRow key={membership.org_id}>
                      <TableCell>
                        <Link
                          to="/$locale/admin/orgs/$id"
                          params={{ locale, id: membership.org_id }}
                          className="font-medium hover:underline"
                        >
                          {membership.org_name}
                        </Link>
                      </TableCell>
                      <TableCell className="text-foreground-secondary">
                        {membership.role}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </SettingsSection>

          <SettingsSection title={t("common.admin.user_detail.sessions_title")}>
            {detail.sessions.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                {t("common.admin.user_detail.no_sessions")}
              </p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>
                      {t("common.admin.user_detail.col_device")}
                    </TableHead>
                    <TableHead>
                      {t("common.admin.user_detail.col_last_seen")}
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {detail.sessions.map((session) => (
                    <TableRow key={session.id}>
                      <TableCell>{session.device_label ?? "—"}</TableCell>
                      <TableCell className="text-muted-foreground">
                        {formatDate(session.last_seen_at, i18n.language)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </SettingsSection>

          <SettingsSection
            title={t("common.admin.user_detail.audit_heading", {
              count: detail.audit_summary.total_count,
            })}
          >
            {detail.audit_summary.recent_actions.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                {t("common.admin.user_detail.no_audit")}
              </p>
            ) : (
              <div className="border-t border-border">
                {detail.audit_summary.recent_actions.map((entry, index) => (
                  <DataListRow
                    key={`${entry.action}-${String(index)}`}
                    className="grid-cols-[minmax(0,1fr)_auto]"
                  >
                    <span className="truncate font-mono text-[0.8125rem]">
                      {entry.action}
                    </span>
                    <DataListMuted>
                      {formatDate(entry.ts, i18n.language)}
                    </DataListMuted>
                  </DataListRow>
                ))}
              </div>
            )}
          </SettingsSection>

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
    </Page>
  );
}
