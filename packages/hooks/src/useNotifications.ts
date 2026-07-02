import { useCallback, useEffect, useRef, useState } from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import type { InfiniteData } from "@tanstack/react-query";

import {
  getUnreadCountApiV1NotificationsUnreadCountGet,
  getUnreadCountApiV1NotificationsUnreadCountGetQueryKey,
  listNotificationsApiV1NotificationsGet,
  listNotificationsApiV1NotificationsGetQueryKey,
  markAllNotificationsReadApiV1NotificationsReadAllPost,
  markNotificationReadApiV1NotificationsNotificationIdReadPatch,
} from "@forge/api-client";
import type {
  EnvelopeListNotificationPublic,
  NotificationPublic,
} from "@forge/api-client";

// The contract a transport (e.g. apps/web/src/lib/sse.ts) must satisfy to
// feed this hook live updates. Kept as a plain function type -- not an
// import of the transport itself -- so this package stays free of
// browser-only APIs (fetch streaming, document.visibilitychange) that don't
// exist the same way in React Native, matching the no-JSX-imports contract
// the rest of @forge/hooks already follows for cross-platform safety.
export interface NotificationStreamHandlers {
  onNotification: (notification: NotificationPublic) => void;
  onConnectedChange: (connected: boolean) => void;
}
export type NotificationStreamSubscriber = (
  handlers: NotificationStreamHandlers,
) => () => void;

export interface UseNotificationsOptions {
  subscribe?: NotificationStreamSubscriber;
  pageSize?: number;
  // Fired for every notification pushed over the live stream, after the
  // query cache has already been updated -- lets a caller layer a UI
  // side-effect (e.g. a sonner toast) on top without this hook importing
  // any UI library itself.
  onNotification?: (notification: NotificationPublic) => void;
}

export interface UseNotificationsResult {
  notifications: NotificationPublic[];
  unreadCount: number;
  isLoading: boolean;
  isError: boolean;
  hasMore: boolean;
  isLoadingMore: boolean;
  loadMore: () => void;
  markRead: (id: string) => Promise<void>;
  markAllRead: () => Promise<void>;
  isConnected: boolean;
}

type NotificationsPageData = InfiniteData<EnvelopeListNotificationPublic, string | null>;

const DEFAULT_PAGE_SIZE = 50;

function mapNotifications(
  data: NotificationsPageData | undefined,
  fn: (notification: NotificationPublic) => NotificationPublic,
): NotificationsPageData | undefined {
  if (!data) {
    return data;
  }
  return {
    ...data,
    pages: data.pages.map((page) => ({
      ...page,
      data: page.data?.map(fn) ?? page.data,
    })),
  };
}

function prependNotification(
  data: NotificationsPageData | undefined,
  notification: NotificationPublic,
): NotificationsPageData | undefined {
  const [firstPage, ...restPages] = data?.pages ?? [];
  if (!data || !firstPage) {
    return data;
  }
  if (firstPage.data?.some((existing) => existing.id === notification.id)) {
    return data;
  }
  return {
    ...data,
    pages: [{ ...firstPage, data: [notification, ...(firstPage.data ?? [])] }, ...restPages],
  };
}

export function useNotifications(
  options: UseNotificationsOptions = {},
): UseNotificationsResult {
  const { subscribe, pageSize = DEFAULT_PAGE_SIZE, onNotification } = options;
  const queryClient = useQueryClient();
  const [isConnected, setIsConnected] = useState(false);
  const hasConnectedOnceRef = useRef(false);

  const listQuery = useInfiniteQuery({
    queryKey: listNotificationsApiV1NotificationsGetQueryKey(),
    queryFn: async ({ pageParam }: { pageParam: string | null }) => {
      const { data } = await listNotificationsApiV1NotificationsGet({
        query: { after: pageParam ?? undefined, limit: pageSize },
        throwOnError: true,
      });
      return data;
    },
    initialPageParam: null as string | null,
    // undefined (not null) tells TanStack Query there is no next page --
    // the backend's cursor field is `string | null` once a list reaches its
    // end (§0.3 cursor pagination), so a bare `??` would otherwise hand
    // back `null` and leave hasNextPage stuck `true` forever.
    getNextPageParam: (lastPage) => lastPage.meta.next_cursor ?? undefined,
  });

  const unreadCountQuery = useQuery({
    queryKey: getUnreadCountApiV1NotificationsUnreadCountGetQueryKey(),
    queryFn: async () => {
      const { data } = await getUnreadCountApiV1NotificationsUnreadCountGet({
        throwOnError: true,
      });
      return data;
    },
  });

  const markReadMutation = useMutation({
    mutationFn: async (notificationId: string) => {
      await markNotificationReadApiV1NotificationsNotificationIdReadPatch({
        path: { notification_id: notificationId },
        throwOnError: true,
      });
    },
    onSuccess: (_response, notificationId) => {
      queryClient.setQueryData<NotificationsPageData>(
        listNotificationsApiV1NotificationsGetQueryKey(),
        (old) =>
          mapNotifications(old, (notification) =>
            notification.id === notificationId
              ? { ...notification, read_at: new Date().toISOString() }
              : notification,
          ),
      );
      void queryClient.invalidateQueries({
        queryKey: getUnreadCountApiV1NotificationsUnreadCountGetQueryKey(),
      });
    },
  });

  const markAllReadMutation = useMutation({
    mutationFn: async () => {
      await markAllNotificationsReadApiV1NotificationsReadAllPost({
        throwOnError: true,
      });
    },
    onSuccess: () => {
      const now = new Date().toISOString();
      queryClient.setQueryData<NotificationsPageData>(
        listNotificationsApiV1NotificationsGetQueryKey(),
        (old) =>
          mapNotifications(old, (notification) =>
            notification.read_at ? notification : { ...notification, read_at: now },
          ),
      );
      void queryClient.invalidateQueries({
        queryKey: getUnreadCountApiV1NotificationsUnreadCountGetQueryKey(),
      });
    },
  });

  // Read through a ref rather than depending on `onNotification` directly --
  // it's a fresh closure on every render of most callers (e.g. NotificationBell
  // defines it inline), and depending on it would tear down and reopen the
  // SSE connection on every render instead of only when `subscribe` changes.
  const onNotificationRef = useRef(onNotification);
  onNotificationRef.current = onNotification;

  useEffect(() => {
    if (!subscribe) {
      return;
    }
    const unsubscribe = subscribe({
      onNotification: (notification) => {
        queryClient.setQueryData<NotificationsPageData>(
          listNotificationsApiV1NotificationsGetQueryKey(),
          (old) => prependNotification(old, notification),
        );
        void queryClient.invalidateQueries({
          queryKey: getUnreadCountApiV1NotificationsUnreadCountGetQueryKey(),
        });
        onNotificationRef.current?.(notification);
      },
      onConnectedChange: (connected) => {
        setIsConnected(connected);
        if (connected) {
          if (hasConnectedOnceRef.current) {
            // A reconnect, not the first connect -- refetch in case any
            // events were missed while disconnected.
            void queryClient.invalidateQueries({
              queryKey: listNotificationsApiV1NotificationsGetQueryKey(),
            });
            void queryClient.invalidateQueries({
              queryKey: getUnreadCountApiV1NotificationsUnreadCountGetQueryKey(),
            });
          }
          hasConnectedOnceRef.current = true;
        }
      },
    });
    return unsubscribe;
  }, [subscribe, queryClient]);

  const notifications =
    listQuery.data?.pages.flatMap((page) => page.data ?? []) ?? [];

  const loadMore = useCallback(() => {
    void listQuery.fetchNextPage();
  }, [listQuery]);

  const markRead = useCallback(
    async (id: string) => {
      await markReadMutation.mutateAsync(id);
    },
    [markReadMutation],
  );

  const markAllRead = useCallback(async () => {
    await markAllReadMutation.mutateAsync();
  }, [markAllReadMutation]);

  return {
    notifications,
    unreadCount: unreadCountQuery.data?.data?.count ?? 0,
    isLoading: listQuery.isLoading,
    isError: listQuery.isError,
    hasMore: listQuery.hasNextPage,
    isLoadingMore: listQuery.isFetchingNextPage,
    loadMore,
    markRead,
    markAllRead,
    isConnected,
  };
}
