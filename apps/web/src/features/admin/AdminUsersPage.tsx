import { useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { listAdminUsers } from "@forge/api-client";
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
import { getErrorMessage } from "../auth/getErrorMessage";
import { useDebouncedValue } from "./useDebouncedValue";

type ActiveFilter = "" | "true" | "false";

export function AdminUsersPage() {
  const locale = useRouteLocale();
  const { t, i18n } = useTranslation();
  const [search, setSearch] = useState("");
  const [active, setActive] = useState<ActiveFilter>("");
  const debouncedSearch = useDebouncedValue(search);

  const usersQuery = useInfiniteQuery({
    queryKey: ["admin", "users", { q: debouncedSearch, active }],
    queryFn: async ({ pageParam }: { pageParam: string | null }) => {
      const { data } = await listAdminUsers({
        after: pageParam,
        limit: 20,
        q: debouncedSearch || undefined,
        active: active === "" ? undefined : active === "true",
      });
      return data;
    },
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.meta.next_cursor ?? null,
  });

  const users = usersQuery.data?.pages.flatMap((page) => page.data ?? []) ?? [];

  return (
    <Page>
      <PageHeader title={t("common.admin.users.title")} />

      <div className="mb-6 flex flex-wrap gap-3">
        <Input
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
          }}
          placeholder={t("common.admin.users.search_placeholder")}
          className="max-w-sm"
          aria-label={t("common.admin.users.search_placeholder")}
        />
        <select
          value={active}
          onChange={(event) => {
            setActive(event.target.value as ActiveFilter);
          }}
          aria-label={t("common.admin.users.filter_active_label")}
          className="h-9 rounded-md border border-input bg-card px-3 text-sm"
        >
          <option value="">{t("common.admin.users.filter_active_all")}</option>
          <option value="true">
            {t("common.admin.users.filter_active_true")}
          </option>
          <option value="false">
            {t("common.admin.users.filter_active_false")}
          </option>
        </select>
      </div>

      <div className="space-y-4">
        {usersQuery.isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : usersQuery.isError ? (
          <Alert variant="destructive" data-testid="admin-users-error">
            <AlertDescription>
              {getErrorMessage(
                usersQuery.error,
                "common.admin.users.error_load",
              )}
            </AlertDescription>
          </Alert>
        ) : users.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {t("common.admin.users.no_results")}
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("common.admin.users.col_email")}</TableHead>
                <TableHead>{t("common.admin.users.col_name")}</TableHead>
                <TableHead>{t("common.admin.users.col_status")}</TableHead>
                <TableHead>{t("common.admin.users.col_created")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {users.map((user) => (
                <TableRow key={user.id}>
                  <TableCell>
                    <Link
                      to="/$locale/admin/users/$id"
                      params={{ locale, id: user.id }}
                      className="font-medium hover:underline"
                    >
                      {user.email}
                    </Link>
                  </TableCell>
                  <TableCell>{user.full_name ?? "—"}</TableCell>
                  <TableCell>
                    <StatusPill tone={user.is_active ? "active" : "neutral"}>
                      {user.is_active
                        ? t("common.admin.users.status_active")
                        : t("common.admin.users.status_inactive")}
                    </StatusPill>
                  </TableCell>
                  <TableCell>
                    {formatDate(user.created_at, i18n.language)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}

        {usersQuery.hasNextPage ? (
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              void usersQuery.fetchNextPage();
            }}
            disabled={usersQuery.isFetchingNextPage}
          >
            {t("common.admin.users.load_more")}
          </Button>
        ) : null}
      </div>
    </Page>
  );
}
