import { useEffect, useRef } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useTranslation } from "react-i18next";

import type { NotificationPublic } from "@forge/api-client";
import { Alert, AlertDescription, Button, Skeleton } from "@forge/ui";
import { formatRelativeTime } from "@forge/utils";

const ESTIMATED_ROW_HEIGHT_PX = 76;
const MAX_FEED_HEIGHT_PX = 384;
const LOAD_MORE_OVERSCAN = 3;

interface NotificationFeedProps {
  notifications: NotificationPublic[];
  isLoading: boolean;
  isError: boolean;
  hasMore: boolean;
  isLoadingMore: boolean;
  onLoadMore: () => void;
  onMarkRead: (id: string) => void;
}

function NotificationRow({
  notification,
  onMarkRead,
}: {
  notification: NotificationPublic;
  onMarkRead: (id: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const isUnread = notification.read_at === null;

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
            className="h-auto px-2 py-0.5 text-xs"
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
        <span className="text-xs text-muted-foreground">
          {formatRelativeTime(notification.created_at, i18n.language)}
        </span>
        {notification.action_url ? (
          <a
            href={notification.action_url}
            className="text-xs font-medium text-primary underline-offset-4 hover:underline"
            onClick={() => {
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
}: NotificationFeedProps) {
  const { t } = useTranslation();
  const scrollRef = useRef<HTMLDivElement>(null);

  const virtualizer = useVirtualizer({
    count: notifications.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ESTIMATED_ROW_HEIGHT_PX,
    overscan: 5,
  });

  const virtualItems = virtualizer.getVirtualItems();
  const lastVirtualItemIndex = virtualItems[virtualItems.length - 1]?.index ?? -1;
  const isNearEnd = lastVirtualItemIndex >= notifications.length - 1 - LOAD_MORE_OVERSCAN;

  useEffect(() => {
    if (isNearEnd && hasMore && !isLoadingMore) {
      onLoadMore();
    }
  }, [isNearEnd, hasMore, isLoadingMore, onLoadMore]);

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
    <div ref={scrollRef} className="overflow-y-auto" style={{ maxHeight: MAX_FEED_HEIGHT_PX }}>
      <div className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
        {virtualItems.map((virtualItem) => {
          const notification = notifications[virtualItem.index];
          if (!notification) {
            return null;
          }
          return (
            <div
              key={notification.id}
              data-index={virtualItem.index}
              ref={virtualizer.measureElement}
              className="absolute left-0 top-0 w-full"
              style={{ transform: `translateY(${String(virtualItem.start)}px)` }}
            >
              <NotificationRow notification={notification} onMarkRead={onMarkRead} />
            </div>
          );
        })}
      </div>
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
