import { useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { listAdminAuditFeed } from "@forge/api-client";
import {
  Alert,
  AlertDescription,
  Button,
  Input,
  Label,
  Page,
  PageHeader,
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
import { useDebouncedValue } from "./useDebouncedValue";

export function AdminAuditPage() {
  const locale = useRouteLocale();
  const { t, i18n } = useTranslation();
  const [actor, setActor] = useState("");
  const [action, setAction] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const debouncedActor = useDebouncedValue(actor);
  const debouncedAction = useDebouncedValue(action);

  const auditQuery = useInfiniteQuery({
    queryKey: [
      "admin",
      "audit",
      { actor: debouncedActor, action: debouncedAction, since, until },
    ],
    queryFn: async ({ pageParam }: { pageParam: string | null }) => {
      const { data } = await listAdminAuditFeed({
        after: pageParam,
        limit: 20,
        actor: debouncedActor || undefined,
        action: debouncedAction || undefined,
        since: since ? new Date(since).toISOString() : undefined,
        until: until ? new Date(until).toISOString() : undefined,
      });
      return data;
    },
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.meta.next_cursor ?? null,
  });

  const events =
    auditQuery.data?.pages.flatMap((page) => page.data ?? []) ?? [];

  return (
    <Page>
      <PageHeader title={t("common.admin.audit.title")} />

      <div className="mb-6 grid grid-cols-2 items-end gap-3 sm:grid-cols-4">
        <Input
          value={actor}
          onChange={(event) => {
            setActor(event.target.value);
          }}
          placeholder={t("common.admin.audit.filter_actor_placeholder")}
          aria-label={t("common.admin.audit.filter_actor_placeholder")}
        />
        <Input
          value={action}
          onChange={(event) => {
            setAction(event.target.value);
          }}
          placeholder={t("common.admin.audit.filter_action_placeholder")}
          aria-label={t("common.admin.audit.filter_action_placeholder")}
        />
        <div className="space-y-1">
          <Label htmlFor="audit-since" className="text-xs">
            {t("common.admin.audit.filter_since_label")}
          </Label>
          <Input
            id="audit-since"
            type="date"
            value={since}
            onChange={(event) => {
              setSince(event.target.value);
            }}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="audit-until" className="text-xs">
            {t("common.admin.audit.filter_until_label")}
          </Label>
          <Input
            id="audit-until"
            type="date"
            value={until}
            onChange={(event) => {
              setUntil(event.target.value);
            }}
          />
        </div>
      </div>

      <div className="space-y-4">
        {auditQuery.isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : auditQuery.isError ? (
          <Alert variant="destructive">
            <AlertDescription>
              {getErrorMessage(
                auditQuery.error,
                "common.admin.audit.error_load",
              )}
            </AlertDescription>
          </Alert>
        ) : events.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {t("common.admin.audit.no_results")}
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("common.admin.audit.col_time")}</TableHead>
                <TableHead>{t("common.admin.audit.col_actor")}</TableHead>
                <TableHead>{t("common.admin.audit.col_action")}</TableHead>
                <TableHead>{t("common.admin.audit.col_org")}</TableHead>
                <TableHead>{t("common.admin.audit.col_severity")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {events.map((event) => (
                <TableRow key={event.id}>
                  <TableCell className="whitespace-nowrap text-muted-foreground">
                    {formatDate(event.ts, i18n.language)}
                  </TableCell>
                  <TableCell>
                    {event.actor_email ?? t("common.admin.audit.system_actor")}
                  </TableCell>
                  <TableCell className="font-mono text-[0.8125rem]">
                    {event.action}
                  </TableCell>
                  <TableCell>
                    {event.org_id && event.org_name ? (
                      <Link
                        to="/$locale/admin/orgs/$id"
                        params={{ locale, id: event.org_id }}
                        className="hover:underline"
                      >
                        {event.org_name}
                      </Link>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell>
                    <StatusPill
                      tone={
                        event.severity === "elevated" ? "danger" : "neutral"
                      }
                    >
                      {event.severity}
                    </StatusPill>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}

        {auditQuery.hasNextPage ? (
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              void auditQuery.fetchNextPage();
            }}
            disabled={auditQuery.isFetchingNextPage}
          >
            {t("common.admin.audit.load_more")}
          </Button>
        ) : null}
      </div>
    </Page>
  );
}
