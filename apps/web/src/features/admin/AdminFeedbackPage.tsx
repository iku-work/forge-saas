import { useState } from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { listAdminFeedback, setAdminFeedbackResolved } from "@forge/api-client";
import type { AdminFeedbackItem } from "@forge/api-client";
import {
  Alert,
  AlertDescription,
  Button,
  Page,
  PageHeader,
  Skeleton,
  StatusPill,
  Tabs,
  TabsList,
  TabsTrigger,
  toast,
} from "@forge/ui";
import { formatDate } from "@forge/utils";

import { useRouteLocale } from "../../lib/useRouteLocale";
import { getErrorMessage } from "../auth/getErrorMessage";

type Status = "open" | "resolved";

/** The Feedback inbox: what users sent from the app, open or handled. */
export function AdminFeedbackPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<Status>("open");

  const feedbackQuery = useInfiniteQuery({
    queryKey: ["admin", "feedback", status],
    queryFn: async ({ pageParam }: { pageParam: string | null }) => {
      const { data } = await listAdminFeedback({
        status,
        after: pageParam,
        limit: 20,
      });
      return data;
    },
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.meta.next_cursor ?? null,
  });

  const toggle = useMutation({
    mutationFn: async (item: AdminFeedbackItem) => {
      await setAdminFeedbackResolved(item.id, item.resolved_at === null);
    },
    onSuccess: async (_data, item) => {
      toast(
        t(
          item.resolved_at === null
            ? "common.admin.feedback.resolved"
            : "common.admin.feedback.reopened",
        ),
      );
      await queryClient.invalidateQueries({ queryKey: ["admin", "feedback"] });
    },
    onError: (error) => {
      toast(getErrorMessage(error, "common.admin.feedback.error_update"));
    },
  });

  const items =
    feedbackQuery.data?.pages.flatMap((page) => page.data ?? []) ?? [];

  return (
    <Page>
      <PageHeader title={t("common.admin.feedback.title")} />

      <Tabs
        value={status}
        onValueChange={(value) => {
          setStatus(value as Status);
        }}
        className="mb-6"
      >
        <TabsList>
          <TabsTrigger value="open">
            {t("common.admin.feedback.tab_open")}
          </TabsTrigger>
          <TabsTrigger value="resolved">
            {t("common.admin.feedback.tab_resolved")}
          </TabsTrigger>
        </TabsList>
      </Tabs>

      <div className="space-y-4">
        {feedbackQuery.isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : feedbackQuery.isError ? (
          <Alert variant="destructive">
            <AlertDescription>
              {getErrorMessage(
                feedbackQuery.error,
                "common.admin.feedback.error_load",
              )}
            </AlertDescription>
          </Alert>
        ) : items.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {t(
              status === "open"
                ? "common.admin.feedback.empty_open"
                : "common.admin.feedback.empty_resolved",
            )}
          </p>
        ) : (
          <ul className="space-y-3">
            {items.map((item) => (
              <FeedbackCard
                key={item.id}
                item={item}
                busy={toggle.isPending}
                onToggle={() => {
                  toggle.mutate(item);
                }}
              />
            ))}
          </ul>
        )}

        {feedbackQuery.hasNextPage ? (
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              void feedbackQuery.fetchNextPage();
            }}
            disabled={feedbackQuery.isFetchingNextPage}
          >
            {t("common.admin.feedback.load_more")}
          </Button>
        ) : null}
      </div>
    </Page>
  );
}

function FeedbackCard({
  item,
  busy,
  onToggle,
}: {
  item: AdminFeedbackItem;
  busy: boolean;
  onToggle: () => void;
}) {
  const { t, i18n } = useTranslation();
  const locale = useRouteLocale();

  return (
    <li
      className="rounded-lg border border-border bg-card p-4"
      data-testid="feedback-item"
    >
      <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        <StatusPill tone={item.category === "bug" ? "pending" : "neutral"}>
          {t(`common.feedback.category.${item.category}`)}
        </StatusPill>
        {item.user_email ? (
          <Link
            to="/$locale/admin/users/$id"
            params={{ locale, id: item.user_id }}
            className="font-medium hover:underline"
          >
            {item.user_email}
          </Link>
        ) : (
          <span className="text-muted-foreground">
            {t("common.admin.feedback.deleted_user")}
          </span>
        )}
        {item.org_id && item.org_name ? (
          <Link
            to="/$locale/admin/orgs/$id"
            params={{ locale, id: item.org_id }}
            className="text-muted-foreground hover:underline"
          >
            {item.org_name}
          </Link>
        ) : null}
        <span className="text-muted-foreground">
          {formatDate(item.created_at, i18n.language)}
        </span>
      </div>
      <p className="whitespace-pre-wrap break-words text-sm">{item.message}</p>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <span className="font-mono text-xs text-muted-foreground">
          {item.page ?? ""}
        </span>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={onToggle}
        >
          {t(
            item.resolved_at === null
              ? "common.admin.feedback.resolve"
              : "common.admin.feedback.reopen",
          )}
        </Button>
      </div>
    </li>
  );
}
