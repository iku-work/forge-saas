import { useCallback, useState } from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  ActivityIndicator,
  FlatList,
  Linking,
  Pressable,
  RefreshControl,
  Text,
  View,
} from "react-native";

import {
  deleteFileApiV1FilesFileIdDelete,
  downloadFileApiV1FilesFileIdDownloadGet,
  listFilesApiV1FilesGet,
  listFilesApiV1FilesGetQueryKey,
  type StoredFilePublic,
} from "@forge/api-client";
import { formatBytes } from "@forge/utils";

import { Banner } from "../../components/Banner";
import { Button } from "../../components/Button";
import { client } from "../../lib/api";
import { ensureCurrentOrg } from "../../lib/org";
import {
  pickAndUpload,
  UploadPermissionError,
  type UploadSource,
} from "../../lib/uploads";
import { UploadSheet } from "./UploadSheet";

const PAGE_SIZE = 20;

type FileStatus = StoredFilePublic["status"];

function statusKey(status: FileStatus): string {
  return `files.status.${status}`;
}

function FileRow({
  file,
  onDownload,
  onDelete,
}: {
  file: StoredFilePublic;
  onDownload: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  const { t } = useTranslation();

  return (
    <View className="border-b border-slate-100 px-4 py-3">
      <Text className="text-sm font-medium text-slate-900">
        {file.filename}
      </Text>
      <Text className="mt-1 text-xs text-slate-400">
        {formatBytes(file.size_bytes)} · {t(statusKey(file.status))}
      </Text>
      <View className="mt-2 flex-row gap-4">
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={t("files.action.download")}
          accessibilityHint={t("files.action.download")}
          onPress={() => {
            onDownload(file.id);
          }}
        >
          <Text className="text-xs font-medium text-slate-900 underline">
            {t("files.action.download")}
          </Text>
        </Pressable>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={t("files.action.delete")}
          accessibilityHint={t("files.action.delete_confirm")}
          onPress={() => {
            onDelete(file.id);
          }}
        >
          <Text className="text-xs font-medium text-red-600 underline">
            {t("files.action.delete")}
          </Text>
        </Pressable>
      </View>
    </View>
  );
}

export function FilesScreen() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [sheetVisible, setSheetVisible] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const orgQuery = useQuery({
    queryKey: ["current-org"],
    queryFn: async () => {
      await ensureCurrentOrg();
      return true;
    },
  });
  const orgReady = orgQuery.isSuccess;

  const listQuery = useInfiniteQuery({
    queryKey: listFilesApiV1FilesGetQueryKey({ client }),
    queryFn: async ({ pageParam }: { pageParam: string | null }) => {
      const { data } = await listFilesApiV1FilesGet({
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

  const invalidateList = useCallback(async () => {
    await queryClient.invalidateQueries({
      queryKey: listFilesApiV1FilesGetQueryKey({ client }),
    });
  }, [queryClient]);

  const deleteMutation = useMutation({
    mutationFn: async (fileId: string) => {
      await deleteFileApiV1FilesFileIdDelete({
        client,
        path: { file_id: fileId },
        throwOnError: true,
      });
    },
    onSuccess: invalidateList,
  });

  const handleDownload = useCallback((fileId: string) => {
    void (async () => {
      const { data } = await downloadFileApiV1FilesFileIdDownloadGet({
        client,
        path: { file_id: fileId },
        throwOnError: true,
      });
      const url = data.data?.url;
      if (url) {
        await Linking.openURL(url);
      }
    })();
  }, []);

  const handleDelete = useCallback(
    (fileId: string) => {
      void deleteMutation.mutateAsync(fileId);
    },
    [deleteMutation],
  );

  const handleSelectSource = useCallback(
    (source: UploadSource) => {
      setSheetVisible(false);
      setUploadError(null);
      setIsUploading(true);
      const handle = pickAndUpload({ source });
      void handle.result
        .then(async (uploaded) => {
          if (uploaded) {
            await invalidateList();
          }
        })
        .catch((error: unknown) => {
          setUploadError(
            error instanceof UploadPermissionError
              ? t("files.picker.permission_denied")
              : t("files.picker.error_upload"),
          );
        })
        .finally(() => {
          setIsUploading(false);
        });
    },
    [invalidateList, t],
  );

  const files = listQuery.data?.pages.flatMap((page) => page.data ?? []) ?? [];

  return (
    <View className="flex-1 bg-white">
      <View className="flex-row items-center justify-between border-b border-slate-100 px-4 py-3">
        <Text className="text-lg font-semibold text-slate-900">
          {t("files.page.title")}
        </Text>
        <Button
          label={t("files.picker.upload_button")}
          hint={t("files.picker.upload_hint")}
          variant="outline"
          disabled={isUploading}
          onPress={() => {
            setSheetVisible(true);
          }}
        />
      </View>

      {uploadError ? (
        <View className="p-4">
          <Banner variant="error">{uploadError}</Banner>
        </View>
      ) : null}
      {isUploading ? (
        <View className="items-center py-3">
          <ActivityIndicator />
        </View>
      ) : null}

      {orgQuery.isPending || listQuery.isPending ? (
        <View className="flex-1 items-center justify-center">
          <ActivityIndicator />
        </View>
      ) : orgQuery.isError || listQuery.isError ? (
        <View className="flex-1 bg-white p-4">
          <Banner variant="error">{t("files.page.error_load")}</Banner>
        </View>
      ) : (
        <FlatList
          data={files}
          keyExtractor={(item) => item.id}
          renderItem={({ item }) => (
            <FileRow
              file={item}
              onDownload={handleDownload}
              onDelete={handleDelete}
            />
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
              {t("files.page.empty")}
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
      )}

      <UploadSheet
        visible={sheetVisible}
        onSelect={handleSelectSource}
        onClose={() => {
          setSheetVisible(false);
        }}
      />
    </View>
  );
}
