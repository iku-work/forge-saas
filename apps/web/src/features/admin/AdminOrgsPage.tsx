import { useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { listAdminOrgs } from "@forge/api-client";
import {
  Alert,
  AlertDescription,
  Button,
  Input,
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
import { subscriptionStatusLabel, subscriptionTone } from "./statusLabels";
import { getErrorMessage } from "../auth/getErrorMessage";
import { useDebouncedValue } from "../../lib/useDebouncedValue";

const STATUS_OPTIONS = [
  "active",
  "trialing",
  "past_due",
  "canceled",
  "incomplete",
];

export function AdminOrgsPage() {
  const locale = useRouteLocale();
  const { t, i18n } = useTranslation();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const debouncedSearch = useDebouncedValue(search);

  const orgsQuery = useInfiniteQuery({
    queryKey: ["admin", "orgs", { q: debouncedSearch, status }],
    queryFn: async ({ pageParam }: { pageParam: string | null }) => {
      const { data } = await listAdminOrgs({
        after: pageParam,
        limit: 20,
        q: debouncedSearch || undefined,
        status: status || undefined,
      });
      return data;
    },
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.meta.next_cursor ?? null,
  });

  const orgs = orgsQuery.data?.pages.flatMap((page) => page.data ?? []) ?? [];

  return (
    <Page>
      <PageHeader title={t("common.admin.orgs.title")} />

      <div className="mb-6 flex flex-wrap gap-3">
        <Input
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
          }}
          placeholder={t("common.admin.orgs.search_placeholder")}
          className="max-w-sm"
          aria-label={t("common.admin.orgs.search_placeholder")}
        />
        <select
          value={status}
          onChange={(event) => {
            setStatus(event.target.value);
          }}
          aria-label={t("common.admin.orgs.filter_status_label")}
          className="h-9 rounded-md border border-input bg-card px-3 text-sm"
        >
          <option value="">{t("common.admin.orgs.filter_status_all")}</option>
          {STATUS_OPTIONS.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      </div>

      <div className="space-y-4">
        {orgsQuery.isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : orgsQuery.isError ? (
          <Alert variant="destructive">
            <AlertDescription>
              {getErrorMessage(orgsQuery.error, "common.admin.orgs.error_load")}
            </AlertDescription>
          </Alert>
        ) : orgs.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {t("common.admin.orgs.no_results")}
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("common.admin.orgs.col_name")}</TableHead>
                <TableHead>{t("common.admin.orgs.col_plan")}</TableHead>
                <TableHead>{t("common.admin.orgs.col_status")}</TableHead>
                <TableHead>{t("common.admin.orgs.col_created")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {orgs.map((org) => (
                <TableRow key={org.id}>
                  <TableCell>
                    <Link
                      to="/$locale/admin/orgs/$id"
                      params={{ locale, id: org.id }}
                      className="font-medium hover:underline"
                    >
                      {org.name}
                    </Link>
                    <p className="text-xs text-muted-foreground">{org.slug}</p>
                  </TableCell>
                  <TableCell className="font-mono text-[0.8125rem]">
                    {org.plan_slug ?? "—"}
                  </TableCell>
                  <TableCell>
                    {org.subscription_status ? (
                      <StatusPill
                        tone={subscriptionTone(org.subscription_status)}
                      >
                        {subscriptionStatusLabel(t, org.subscription_status)}
                      </StatusPill>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell>
                    {formatDate(org.created_at, i18n.language)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}

        {orgsQuery.hasNextPage ? (
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              void orgsQuery.fetchNextPage();
            }}
            disabled={orgsQuery.isFetchingNextPage}
          >
            {t("common.admin.orgs.load_more")}
          </Button>
        ) : null}
      </div>
    </Page>
  );
}
