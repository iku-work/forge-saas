import { useCallback } from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { router, type Href } from "expo-router";
import { useTranslation } from "react-i18next";
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  RefreshControl,
  Text,
  View,
} from "react-native";

import {
  listNotificationsApiV1NotificationsGet,
  listNotificationsApiV1NotificationsGetQueryKey,
  markAllNotificationsReadApiV1NotificationsReadAllPost,
  markNotificationReadApiV1NotificationsNotificationIdReadPatch,
  type NotificationPublic,
} from "@forge/api-client";
import { formatRelativeTime } from "@forge/utils";

import { Banner } from "../../components/Banner";
import { Button } from "../../components/Button";
import { client } from "../../lib/api";
import { ensureCurrentOrg } from "../../lib/org";

const PAGE_SIZE = 50;

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
    <View className="border-b border-slate-100 px-4 py-3">
      <View className="flex-row items-start justify-between gap-2">
        <Text className="flex-1 text-sm font-medium text-slate-900">
          {notification.title}
        </Text>
        {isUnread ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={t("notifications.feed.mark_read")}
            accessibilityHint={t("notifications.feed.mark_read_hint")}
            onPress={() => {
              onMarkRead(notification.id);
            }}
          >
            <Text className="text-xs font-medium text-slate-500">
              {t("notifications.feed.mark_read")}
            </Text>
          </Pressable>
        ) : null}
      </View>
      <Text className="mt-1 text-sm text-slate-600">{notification.body}</Text>
      <View className="mt-2 flex-row items-center justify-between">
        <Text className="text-xs text-slate-400">
          {formatRelativeTime(notification.created_at, i18n.language)}
        </Text>
        {notification.action_url ? (
          <Pressable
            accessibilityRole="link"
            accessibilityLabel={t("notifications.feed.view")}
            accessibilityHint={t("notifications.feed.view_hint")}
            onPress={() => {
              if (isUnread) {
                onMarkRead(notification.id);
              }
              // Backend-controlled and unknown to the typed-routes generator
              // ahead of time -- same escape hatch lib/push.ts uses for the
              // identical field on a push notification's tap response.
              router.push(notification.action_url as Href);
            }}
          >
            <Text className="text-xs font-medium text-slate-900 underline">
              {t("notifications.feed.view")}
            </Text>
          </Pressable>
        ) : null}
      </View>
    </View>
  );
}

export function NotificationsScreen() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const orgQuery = useQuery({
    queryKey: ["current-org"],
    queryFn: async () => {
      await ensureCurrentOrg();
      return true;
    },
  });
  const orgReady = orgQuery.isSuccess;

  const listQuery = useInfiniteQuery({
    queryKey: listNotificationsApiV1NotificationsGetQueryKey({ client }),
    queryFn: async ({ pageParam }: { pageParam: string | null }) => {
      const { data } = await listNotificationsApiV1NotificationsGet({
        client,
        query: { after: pageParam ?? undefined, limit: PAGE_SIZE },
        throwOnError: true,
      });
      return data;
    },
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.meta.next_cursor ?? undefined,
    enabled: orgReady,
  });

  const markReadMutation = useMutation({
    mutationFn: async (id: string) => {
      await markNotificationReadApiV1NotificationsNotificationIdReadPatch({
        client,
        path: { notification_id: id },
        throwOnError: true,
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: listNotificationsApiV1NotificationsGetQueryKey({ client }),
      });
    },
  });

  const markAllReadMutation = useMutation({
    mutationFn: async () => {
      await markAllNotificationsReadApiV1NotificationsReadAllPost({
        client,
        throwOnError: true,
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: listNotificationsApiV1NotificationsGetQueryKey({ client }),
      });
    },
  });

  const handleMarkRead = useCallback(
    (id: string) => {
      void markReadMutation.mutateAsync(id);
    },
    [markReadMutation],
  );

  const notifications =
    listQuery.data?.pages.flatMap((page) => page.data ?? []) ?? [];

  if (orgQuery.isPending || listQuery.isPending) {
    return (
      <View className="flex-1 items-center justify-center bg-white">
        <ActivityIndicator />
      </View>
    );
  }

  if (orgQuery.isError || listQuery.isError) {
    return (
      <View className="flex-1 bg-white p-4">
        <Banner variant="error">{t("notifications.feed.error")}</Banner>
      </View>
    );
  }

  return (
    <View className="flex-1 bg-white">
      <View className="flex-row items-center justify-between border-b border-slate-100 px-4 py-3">
        <Text className="text-lg font-semibold text-slate-900">
          {t("notifications.feed.heading")}
        </Text>
        <Button
          label={t("notifications.feed.mark_all_read")}
          hint={t("notifications.feed.mark_all_read_hint")}
          variant="outline"
          onPress={() => {
            void markAllReadMutation.mutateAsync();
          }}
        />
      </View>
      <FlatList
        data={notifications}
        keyExtractor={(item) => item.id}
        renderItem={({ item }) => (
          <NotificationRow notification={item} onMarkRead={handleMarkRead} />
        )}
        refreshControl={
          <RefreshControl
            refreshing={listQuery.isRefetching}
            onRefresh={() => {
              void listQuery.refetch();
            }}
          />
        }
        onEndReached={() => {
          if (listQuery.hasNextPage && !listQuery.isFetchingNextPage) {
            void listQuery.fetchNextPage();
          }
        }}
        onEndReachedThreshold={0.5}
        ListEmptyComponent={
          <Text className="p-6 text-center text-sm text-slate-400">
            {t("notifications.feed.empty")}
          </Text>
        }
        ListFooterComponent={
          listQuery.isFetchingNextPage ? (
            <View className="p-4">
              <ActivityIndicator />
            </View>
          ) : null
        }
      />
    </View>
  );
}
