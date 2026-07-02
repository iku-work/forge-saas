import { useState } from "react";
import { Bell } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useNotifications } from "@forge/hooks";
import {
  Badge,
  Button,
  Popover,
  PopoverContent,
  PopoverTrigger,
  Separator,
  toast,
} from "@forge/ui";

import { subscribeToNotificationStream } from "../../lib/sse";
import { NotificationFeed } from "./NotificationFeed";

const MAX_BADGE_COUNT = 99;

export function NotificationBell() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  const {
    notifications,
    unreadCount,
    isLoading,
    isError,
    hasMore,
    isLoadingMore,
    loadMore,
    markRead,
    markAllRead,
  } = useNotifications({
    subscribe: subscribeToNotificationStream,
    // The stream itself only stays open while the tab is visible (see
    // lib/sse.ts), so this guard is mostly documentation of intent -- it's
    // cheap insurance against a toast slipping through during the brief
    // window around a visibility flip.
    onNotification: (notification) => {
      if (typeof document !== "undefined" && document.hidden) {
        return;
      }
      toast(notification.title, { description: notification.body });
    },
  });

  const ariaLabel =
    unreadCount > 0
      ? t("notifications.bell.aria_label_unread", { count: unreadCount })
      : t("notifications.bell.aria_label");

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={ariaLabel}
          className="relative rounded-md p-2 text-muted-foreground hover:bg-accent hover:text-accent-foreground"
        >
          <Bell className="size-4" aria-hidden="true" />
          {unreadCount > 0 ? (
            <Badge
              variant="destructive"
              className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] leading-none"
            >
              {unreadCount > MAX_BADGE_COUNT ? `${String(MAX_BADGE_COUNT)}+` : unreadCount}
            </Badge>
          ) : null}
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        className="w-96 p-0"
        aria-label={t("notifications.feed.heading")}
      >
        <div className="flex items-center justify-between px-4 py-2">
          <p className="text-sm font-semibold">{t("notifications.feed.heading")}</p>
          {unreadCount > 0 ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-auto px-2 py-0.5 text-xs"
              onClick={() => {
                void markAllRead();
              }}
            >
              {t("notifications.feed.mark_all_read")}
            </Button>
          ) : null}
        </div>
        <Separator />
        <NotificationFeed
          notifications={notifications}
          isLoading={isLoading}
          isError={isError}
          hasMore={hasMore}
          isLoadingMore={isLoadingMore}
          onLoadMore={loadMore}
          onMarkRead={(id) => {
            void markRead(id);
          }}
        />
      </PopoverContent>
    </Popover>
  );
}
