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
    markUnread,
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
          data-testid="notification-bell-trigger"
          className="relative rounded-md p-2 text-muted-foreground hover:bg-accent hover:text-accent-foreground"
        >
          <Bell className="size-4" aria-hidden="true" />
          {unreadCount > 0 ? (
            <Badge
              variant="destructive"
              data-testid="notification-badge"
              className="absolute -inset-e-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] leading-none"
            >
              {unreadCount > MAX_BADGE_COUNT
                ? `${String(MAX_BADGE_COUNT)}+`
                : unreadCount}
            </Badge>
          ) : null}
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        className="w-96 p-0"
        aria-label={t("notifications.feed.heading")}
        data-testid="notification-panel"
      >
        <div className="flex items-center justify-between px-4 py-2">
          <p className="text-sm font-semibold">
            {t("notifications.feed.heading")}
          </p>
          {unreadCount > 0 ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              // F-014: h-auto + 12px text + 2px padding measured 23.2px tall, just
              // under WCAG 2.2 2.5.8. min-h-6 is the 24px floor exactly; a full
              // 44px target would stretch every row of a deliberately dense feed.
              className="h-auto min-h-6 px-2 py-0.5 text-xs"
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
            // F-009: act now, offer the way back -- rather than asking first.
            // Marking read is reversible and low-stakes, so a confirmation
            // would interrupt the common case to protect against the rare
            // one. The toast is the protection, and it does not block.
            //
            // The `.catch` is not decoration: markRead() rejects when the
            // request fails, and a bare `void` on it left an unhandled
            // promise rejection behind -- invisible to the user, and enough
            // to fail a test run on its own. The mutation's onError has
            // already put the row back by the time this runs; all that is
            // left is to say so (F-011: every error has a next step).
            markRead(id)
              .then(() => {
                toast(t("notifications.feed.marked_read"), {
                  action: {
                    label: t("common.button.undo"),
                    onClick: () => {
                      markUnread(id).catch(() => {
                        toast.error(t("notifications.feed.error_mark_read"));
                      });
                    },
                  },
                  // Long enough to notice and reach, short enough not to
                  // stack up when someone clears several rows in a row.
                  duration: 5000,
                });
              })
              .catch(() => {
                toast.error(t("notifications.feed.error_mark_read"));
              });
          }}
        />
      </PopoverContent>
    </Popover>
  );
}
