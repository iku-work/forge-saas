import { useTranslation } from "react-i18next";

import type { NotificationPublic } from "@forge/api-client";
import { Alert, AlertDescription, Button, Skeleton } from "@forge/ui";
import { formatRelativeTime } from "@forge/utils";

import { VirtualList } from "../../components/VirtualList";

// Notification rows are variable height (title wraps, body wraps, the
// action link is optional), so this is only the pre-measurement estimate --
// VirtualList measures each mounted row and corrects the offsets.
const ESTIMATED_ROW_HEIGHT_PX = 76;
const MAX_FEED_HEIGHT_PX = 384;

interface NotificationFeedProps {
  notifications: NotificationPublic[];
  isLoading: boolean;
  isError: boolean;
  hasMore: boolean;
  isLoadingMore: boolean;
  onLoadMore: () => void;
  onMarkRead: (id: string) => void;
  /** Org names by id, given only when the reader is in more than one org:
   *  the inbox spans all of them, so each row says where it came from. */
  orgNames?: ReadonlyMap<string, string> | undefined;
  /** Called before following a notification's link, so the app can switch
   *  to the org the notification belongs to. */
  onOpen?: ((notification: NotificationPublic) => void) | undefined;
}

function NotificationRow({
  notification,
  onMarkRead,
  orgNames,
  onOpen,
}: {
  notification: NotificationPublic;
  onMarkRead: (id: string) => void;
  orgNames?: ReadonlyMap<string, string> | undefined;
  onOpen?: ((notification: NotificationPublic) => void) | undefined;
}) {
  const { t, i18n } = useTranslation();
  const isUnread = notification.read_at === null;
  const orgName = notification.org_id
    ? orgNames?.get(notification.org_id)
    : undefined;

  return (
    <div
      className={`flex flex-col gap-1 border-b px-4 py-3 text-sm ${isUnread ? "bg-accent/40" : ""}`}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="font-medium">{notification.title}</p>
        {isUnread ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            // F-014: h-auto + 12px text + 2px padding measured 23.2px tall, just
            // under WCAG 2.2 2.5.8. min-h-6 is the 24px floor exactly; a full
            // 44px target would stretch every row of a deliberately dense feed.
            className="h-auto min-h-6 px-2 py-0.5 text-xs"
            onClick={() => {
              onMarkRead(notification.id);
            }}
          >
            {t("notifications.feed.mark_read")}
          </Button>
        ) : null}
      </div>
      <p className="text-muted-foreground">{notification.body}</p>
      <div className="flex items-center justify-between gap-2">
        <span className="flex min-w-0 gap-2 text-xs text-muted-foreground">
          {orgName ? (
            <span
              className="truncate font-medium"
              data-testid="notification-org"
            >
              {orgName}
            </span>
          ) : null}
          <span className="shrink-0">
            {formatRelativeTime(notification.created_at, i18n.language)}
          </span>
        </span>
        {notification.action_url ? (
          <a
            href={notification.action_url}
            className="text-xs font-medium text-primary underline-offset-4 hover:underline"
            onClick={() => {
              onOpen?.(notification);
              if (isUnread) {
                onMarkRead(notification.id);
              }
            }}
          >
            {t("notifications.feed.view")}
          </a>
        ) : null}
      </div>
    </div>
  );
}

export function NotificationFeed({
  notifications,
  isLoading,
  isError,
  hasMore,
  isLoadingMore,
  onLoadMore,
  onMarkRead,
  orgNames,
  onOpen,
}: NotificationFeedProps) {
  const { t } = useTranslation();

  if (isLoading) {
    return (
      <div className="space-y-2 p-4">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="p-4">
        <Alert variant="destructive">
          <AlertDescription>{t("notifications.feed.error")}</AlertDescription>
        </Alert>
      </div>
    );
  }

  if (notifications.length === 0) {
    return (
      <p className="p-6 text-center text-sm text-muted-foreground">
        {t("notifications.feed.empty")}
      </p>
    );
  }

  return (
    <div>
      <VirtualList
        items={notifications}
        getItemKey={(notification) => notification.id}
        label={t("notifications.feed.list_label")}
        estimateSize={ESTIMATED_ROW_HEIGHT_PX}
        dynamicRowHeight
        maxHeight={MAX_FEED_HEIGHT_PX}
        hasMore={hasMore}
        isLoadingMore={isLoadingMore}
        onLoadMore={onLoadMore}
        itemTestId="notification-row"
        renderItem={(notification) => (
          <NotificationRow
            notification={notification}
            onMarkRead={onMarkRead}
            orgNames={orgNames}
            onOpen={onOpen}
          />
        )}
      />
      {isLoadingMore ? (
        <div className="p-3 text-center text-xs text-muted-foreground">
          {t("notifications.feed.loading")}
        </div>
      ) : hasMore ? (
        <div className="p-2 text-center">
          <Button type="button" variant="ghost" size="sm" onClick={onLoadMore}>
            {t("notifications.feed.load_more")}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
