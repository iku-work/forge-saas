import { useState } from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { FolderOpen } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  deleteFileApiV1FilesFileIdDelete,
  downloadFileApiV1FilesFileIdDownloadGet,
  listFilesApiV1FilesGet,
  listFilesApiV1FilesGetInfiniteQueryKey,
} from "@forge/api-client";
import type { StoredFilePublic } from "@forge/api-client";
import { usePermission, useOrg } from "@forge/hooks";
import { Permission } from "@forge/types";
import {
  Alert,
  AlertDescription,
  Avatar,
  AvatarFallback,
  Badge,
  Button,
  ConfirmDialog,
  EmptyState,
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

import {
  displayStatus,
  scanPollInterval,
  type DisplayStatus,
} from "./fileStatus";

import { getErrorMessage } from "../auth/getErrorMessage";

// The same condition the server applies in scan_gate.require_clean_scan.
// Duplicated here knowingly: the server is still the authority and refuses
// anything else, this only stops the page offering a button that 409s.
function isDownloadable(file: StoredFilePublic): boolean {
  return file.status === "ready" && file.virus_scan_result === "clean";
}

function statusVariant(
  status: DisplayStatus,
): "default" | "secondary" | "destructive" | "outline" {
  if (status === "ready") return "default";
  if (status === "infected" || status === "failed") return "destructive";
  return "secondary";
}

function statusKey(status: DisplayStatus): string {
  return `files.status.${status}`;
}

// Why the download button is off, in the user's words. Mapped explicitly
// rather than interpolated: `StoredFilePublic.status` is typed `string` on
// the wire, so an unmapped value would render a raw translation key in the
// tooltip instead of a sentence.
function downloadBlockedKey(status: DisplayStatus): string {
  if (status === "scanning") return "files.action.download_blocked_scanning";
  if (status === "infected") return "files.action.download_blocked_infected";
  if (status === "failed") return "files.action.download_blocked_failed";
  return "files.action.download_blocked_pending";
}

function uploaderInitials(uploaderId: string): string {
  return uploaderId.slice(0, 2).toUpperCase();
}

export function FilesPage() {
  const { t } = useTranslation();
  const { org, isLoading: orgLoading } = useOrg();
  // A MEMBER has files:read but not files:manage, so the uploader and the
  // delete buttons were offering them two actions the API answers with 403.
  // The server is still the gate; this stops the page lying about what the
  // person in front of it can do.
  const canManage = usePermission(Permission.FILES_MANAGE);
  const queryClient = useQueryClient();
  // Download and delete both used to discard their failures -- `void` on
  // the promise, no catch -- so a 409 on an unscanned file or a 403 on a
  // delete produced no feedback whatsoever. One slot, because only one of
  // these can be in flight from a single click.
  const [actionError, setActionError] = useState<string | null>(null);
  // The file awaiting confirmation, or null. Holds the whole file rather than
  // its id so the dialog can name it -- "Delete report-q3.pdf?" is a question
  // the user can actually answer, which "Delete this file?" (what the old
  // window.confirm asked) is not.
  const [pendingDelete, setPendingDelete] = useState<StoredFilePublic | null>(
    null,
  );

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
    refetchInterval: (query) =>
      scanPollInterval(
        query.state.data?.pages.flatMap((page) => page.data ?? []) ?? [],
        query.state.dataUpdatedAt,
      ),
  });

  const deleteMutation = useMutation({
    mutationFn: async (fileId: string) => {
      await deleteFileApiV1FilesFileIdDelete({
        path: { file_id: fileId },
        throwOnError: true,
      });
    },
    onSuccess: async () => {
      setActionError(null);
      await queryClient.invalidateQueries({
        queryKey: listFilesApiV1FilesGetInfiniteQueryKey(),
      });
    },
    onError: (error) => {
      setActionError(getErrorMessage(error, "files.page.error_delete"));
    },
  });

  async function handleDownload(fileId: string): Promise<void> {
    setActionError(null);
    try {
      const { data } = await downloadFileApiV1FilesFileIdDownloadGet({
        path: { file_id: fileId },
        throwOnError: true,
      });
      const url = data.data?.url;
      if (url) window.open(url, "_blank", "noopener,noreferrer");
    } catch (error) {
      setActionError(getErrorMessage(error, "files.page.error_download"));
    }
  }

  const files = filesQuery.data?.pages.flatMap((page) => page.data ?? []) ?? [];
  // Polling has given up on a scan that is still pending (see
  // scanPollInterval) -- say so instead of leaving "Scanning" to look stuck.
  const scanTakingLong =
    files.some((file) => displayStatus(file) === "scanning") &&
    scanPollInterval(files, filesQuery.dataUpdatedAt) === false;

  return (
    <div className="space-y-6 p-6">
      <h1 className="text-xl font-semibold" data-testid="page-title">
        {t("files.page.title")}
      </h1>

      {canManage ? (
        <FileUploader
          onUploaded={() => {
            void queryClient.invalidateQueries({
              queryKey: listFilesApiV1FilesGetInfiniteQueryKey(),
            });
          }}
        />
      ) : null}

      {actionError ? (
        <Alert variant="destructive">
          <AlertDescription>{actionError}</AlertDescription>
        </Alert>
      ) : null}

      {scanTakingLong ? (
        <Alert data-testid="scan-taking-long">
          <AlertDescription>
            {t("files.page.scan_taking_long")}
          </AlertDescription>
        </Alert>
      ) : null}

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
        // No `action` here, deliberately: the uploader is already rendered
        // directly above this block, so a button would either duplicate it or
        // scroll the user to it. The description points at it instead.
        <EmptyState
          icon={FolderOpen}
          title={t("files.page.empty_title")}
          description={
            canManage
              ? t("files.page.empty_body")
              : t("files.page.empty_body_read_only")
          }
        />
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
                    <Badge variant={statusVariant(displayStatus(file))}>
                      {t(statusKey(displayStatus(file)))}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-end">
                    <div className="flex justify-end gap-2">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        // Disabled rather than hidden: the row is still
                        // there, and a button that vanishes mid-scan is
                        // more confusing than one that explains itself.
                        disabled={!isDownloadable(file)}
                        title={
                          isDownloadable(file)
                            ? undefined
                            : t(downloadBlockedKey(displayStatus(file)))
                        }
                        onClick={() => void handleDownload(file.id)}
                      >
                        {t("files.action.download")}
                      </Button>
                      {canManage ? (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          disabled={deleteMutation.isPending}
                          onClick={() => {
                            setPendingDelete(file);
                          }}
                        >
                          {t("files.action.delete")}
                        </Button>
                      ) : null}
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

      <ConfirmDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => {
          if (!open) {
            setPendingDelete(null);
          }
        }}
        title={t("files.action.delete_confirm_title")}
        description={t("files.action.delete_confirm_body", {
          filename: pendingDelete?.filename ?? "",
        })}
        confirmLabel={t("files.action.delete")}
        destructive
        isPending={deleteMutation.isPending}
        onConfirm={() => {
          const file = pendingDelete;
          if (!file) {
            return;
          }
          // Closed before the request, not after: the dialog has done its job
          // once the answer is given, and leaving it up during the round trip
          // makes a fast delete look like a stuck one.
          setPendingDelete(null);
          deleteMutation.mutate(file.id);
        }}
      />
    </div>
  );
}
