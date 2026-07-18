import {
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import {
  deleteFileApiV1FilesFileIdDelete,
  downloadFileApiV1FilesFileIdDownloadGet,
  listFilesApiV1FilesGet,
  listFilesApiV1FilesGetInfiniteQueryKey,
} from "@forge/api-client";
import type { StoredFilePublic } from "@forge/api-client";
import { useOrg } from "@forge/hooks";
import {
  Alert,
  AlertDescription,
  Avatar,
  AvatarFallback,
  Badge,
  Button,
  FileUploader,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@forge/ui";
import { formatBytes } from "@forge/utils";

import { getErrorMessage } from "../auth/getErrorMessage";

type StoredFileStatus = StoredFilePublic["status"];

function statusVariant(
  status: StoredFileStatus,
): "default" | "secondary" | "destructive" | "outline" {
  if (status === "ready") return "default";
  if (status === "infected" || status === "failed") return "destructive";
  return "secondary";
}

function statusKey(status: StoredFileStatus): string {
  return `files.status.${status}`;
}

function uploaderInitials(uploaderId: string): string {
  return uploaderId.slice(0, 2).toUpperCase();
}

export function FilesPage() {
  const { t } = useTranslation();
  const { org, isLoading: orgLoading } = useOrg();
  const queryClient = useQueryClient();

  const filesQuery = useInfiniteQuery({
    queryKey: listFilesApiV1FilesGetInfiniteQueryKey(),
    queryFn: async ({ pageParam }) => {
      const { data } = await listFilesApiV1FilesGet({
        query: { after: pageParam, limit: 20 },
        throwOnError: true,
      });
      return data;
    },
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.meta.next_cursor ?? null,
    enabled: Boolean(org),
  });

  const deleteMutation = useMutation({
    mutationFn: async (fileId: string) => {
      await deleteFileApiV1FilesFileIdDelete({
        path: { file_id: fileId },
        throwOnError: true,
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: listFilesApiV1FilesGetInfiniteQueryKey(),
      });
    },
  });

  async function handleDownload(fileId: string): Promise<void> {
    const { data } = await downloadFileApiV1FilesFileIdDownloadGet({
      path: { file_id: fileId },
      throwOnError: true,
    });
    const url = data.data?.url;
    if (url) window.open(url, "_blank", "noopener,noreferrer");
  }

  const files = filesQuery.data?.pages.flatMap((page) => page.data ?? []) ?? [];

  return (
    <div className="space-y-6 p-6">
      <h1 className="text-xl font-semibold">{t("files.page.title")}</h1>

      <FileUploader
        onUploaded={() => {
          void queryClient.invalidateQueries({
            queryKey: listFilesApiV1FilesGetInfiniteQueryKey(),
          });
        }}
      />

      {orgLoading || filesQuery.isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : filesQuery.isError ? (
        <Alert variant="destructive">
          <AlertDescription>
            {getErrorMessage(filesQuery.error, "files.page.error_load")}
          </AlertDescription>
        </Alert>
      ) : files.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("files.page.empty")}</p>
      ) : (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("files.table.col_name")}</TableHead>
                <TableHead>{t("files.table.col_size")}</TableHead>
                <TableHead>{t("files.table.col_uploader")}</TableHead>
                <TableHead>{t("files.table.col_status")}</TableHead>
                <TableHead className="text-end">
                  {t("files.table.col_actions")}
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {files.map((file) => (
                <TableRow key={file.id}>
                  <TableCell className="font-medium">{file.filename}</TableCell>
                  <TableCell className="text-muted-foreground">
                    {formatBytes(file.size_bytes)}
                  </TableCell>
                  <TableCell>
                    <Avatar className="size-6">
                      <AvatarFallback className="text-xs">
                        {uploaderInitials(file.uploaded_by)}
                      </AvatarFallback>
                    </Avatar>
                  </TableCell>
                  <TableCell>
                    <Badge variant={statusVariant(file.status)}>
                      {t(statusKey(file.status))}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-end">
                    <div className="flex justify-end gap-2">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => void handleDownload(file.id)}
                      >
                        {t("files.action.download")}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={deleteMutation.isPending}
                        onClick={() => {
                          if (
                            window.confirm(t("files.action.delete_confirm"))
                          ) {
                            void deleteMutation.mutateAsync(file.id);
                          }
                        }}
                      >
                        {t("files.action.delete")}
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>

          {filesQuery.hasNextPage ? (
            <Button
              type="button"
              variant="outline"
              onClick={() => void filesQuery.fetchNextPage()}
              disabled={filesQuery.isFetchingNextPage}
            >
              {t("common.button.load_more")}
            </Button>
          ) : null}
        </>
      )}
    </div>
  );
}
