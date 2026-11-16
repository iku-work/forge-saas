import { useState } from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import {
  deleteAdminOrgFile,
  listAdminOrgFiles,
  listAdminOrgMembers,
  previewAdminOrgFile,
} from "@forge/api-client";
import type { AdminOrgFile } from "@forge/api-client";
import {
  Alert,
  AlertDescription,
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  Skeleton,
  StatusPill,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  toast,
} from "@forge/ui";
import { formatBytes, formatDate } from "@forge/utils";

import { useRouteLocale } from "../../lib/useRouteLocale";
import { getErrorMessage } from "../auth/getErrorMessage";

const PAGE_SIZE = 20;
const MIN_REASON_LENGTH = 10;

function LoadMore({
  hasMore,
  isLoading,
  onClick,
}: {
  hasMore: boolean;
  isLoading: boolean;
  onClick: () => void;
}) {
  const { t } = useTranslation();
  if (!hasMore) return null;
  return (
    <div className="pt-3 text-center">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        disabled={isLoading}
        onClick={onClick}
      >
        {t("common.admin.org_content.load_more")}
      </Button>
    </div>
  );
}

/** Every member of the org, with a link to each one's admin user page. */
export function AdminOrgMembersTab({ orgId }: { orgId: string }) {
  const { t, i18n } = useTranslation();
  const locale = useRouteLocale();
  const query = useInfiniteQuery({
    queryKey: ["admin", "orgs", orgId, "members"],
    queryFn: async ({ pageParam }: { pageParam: string | null }) => {
      const { data } = await listAdminOrgMembers(orgId, {
        after: pageParam,
        limit: PAGE_SIZE,
      });
      return data;
    },
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.meta.next_cursor ?? undefined,
  });
  const members = query.data?.pages.flatMap((page) => page.data ?? []) ?? [];

  if (query.isLoading) return <Skeleton className="h-32 w-full" />;
  if (query.isError) {
    return (
      <Alert variant="destructive">
        <AlertDescription>
          {getErrorMessage(query.error, "common.admin.org_content.error_load")}
        </AlertDescription>
      </Alert>
    );
  }
  if (members.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        {t("common.admin.org_content.no_members")}
      </p>
    );
  }
  return (
    <div data-testid="admin-org-members">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t("common.admin.org_content.col_email")}</TableHead>
            <TableHead>{t("common.admin.org_content.col_role")}</TableHead>
            <TableHead>{t("common.admin.org_content.col_joined")}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {members.map((member) => (
            <TableRow key={member.user_id}>
              <TableCell>
                <Link
                  to="/$locale/admin/users/$id"
                  params={{ locale, id: member.user_id }}
                  className="font-medium hover:underline"
                >
                  {member.email}
                </Link>
              </TableCell>
              <TableCell className="text-foreground-secondary">
                {t(`tenancy.members.role.${member.role}`)}
              </TableCell>
              <TableCell className="text-muted-foreground">
                {formatDate(member.joined_at, i18n.language)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <LoadMore
        hasMore={query.hasNextPage}
        isLoading={query.isFetchingNextPage}
        onClick={() => {
          void query.fetchNextPage();
        }}
      />
    </div>
  );
}

function statusTone(status: string): "active" | "pending" | "danger" {
  if (status === "clean") return "active";
  if (status === "uploading" || status === "scanning") return "pending";
  return "danger";
}

/**
 * The org's files, without their content. A clean file can be opened
 * (images and PDF in the browser, anything else downloads) after a recent
 * sign-in; any file can be removed, with a reason, which tells the owners.
 */
export function AdminOrgFilesTab({ orgId }: { orgId: string }) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const [removing, setRemoving] = useState<AdminOrgFile | null>(null);
  const [reason, setReason] = useState("");
  const queryKey = ["admin", "orgs", orgId, "files"];

  const query = useInfiniteQuery({
    queryKey,
    queryFn: async ({ pageParam }: { pageParam: string | null }) => {
      const { data } = await listAdminOrgFiles(orgId, {
        after: pageParam,
        limit: PAGE_SIZE,
      });
      return data;
    },
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.meta.next_cursor ?? undefined,
  });
  const files = query.data?.pages.flatMap((page) => page.data ?? []) ?? [];

  const preview = useMutation({
    mutationFn: async (file: AdminOrgFile) => {
      // Opened before the request, while the click still counts as the
      // user's: a window opened after an await is a blocked pop-up. It is
      // cut off from this page before it is pointed anywhere.
      const tab = window.open("", "_blank");
      try {
        const { data } = await previewAdminOrgFile(orgId, file.id);
        if (tab && data.data) {
          tab.opener = null;
          tab.location.href = data.data.url;
        }
      } catch (error) {
        tab?.close();
        throw error;
      }
    },
    onError: (error) => {
      toast.error(
        getErrorMessage(error, "common.admin.org_content.error_preview"),
      );
    },
  });

  const remove = useMutation({
    mutationFn: async (file: AdminOrgFile) => {
      await deleteAdminOrgFile(orgId, file.id, reason.trim());
    },
    onSuccess: async () => {
      toast(t("common.admin.org_content.removed"));
      setRemoving(null);
      setReason("");
      await queryClient.invalidateQueries({ queryKey });
    },
  });

  if (query.isLoading) return <Skeleton className="h-32 w-full" />;
  if (query.isError) {
    return (
      <Alert variant="destructive">
        <AlertDescription>
          {getErrorMessage(query.error, "common.admin.org_content.error_load")}
        </AlertDescription>
      </Alert>
    );
  }
  if (files.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        {t("common.admin.org_content.no_files")}
      </p>
    );
  }

  const reasonValid = reason.trim().length >= MIN_REASON_LENGTH;

  return (
    <div data-testid="admin-org-files">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t("common.admin.org_content.col_file")}</TableHead>
            <TableHead>{t("common.admin.org_content.col_status")}</TableHead>
            <TableHead>{t("common.admin.org_content.col_uploaded")}</TableHead>
            <TableHead className="text-end">
              {t("common.admin.org_content.col_actions")}
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {files.map((file) => (
            <TableRow key={file.id} data-testid="admin-org-file-row">
              <TableCell>
                <p className="break-all font-medium">{file.filename}</p>
                <p className="text-xs text-muted-foreground">
                  {file.content_type}
                  <span className="ms-2">{formatBytes(file.size_bytes)}</span>
                </p>
              </TableCell>
              <TableCell>
                <StatusPill tone={statusTone(file.status)}>
                  {t(`common.admin.org_content.status_${file.status}`, {
                    defaultValue: file.status,
                  })}
                </StatusPill>
              </TableCell>
              <TableCell className="text-muted-foreground">
                <p className="break-all">{file.uploaded_by_email}</p>
                <p className="text-xs">
                  {formatDate(file.created_at, i18n.language)}
                </p>
              </TableCell>
              <TableCell className="text-end">
                <div className="flex justify-end gap-2">
                  {file.status === "clean" ? (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={preview.isPending}
                      onClick={() => {
                        preview.mutate(file);
                      }}
                    >
                      {t("common.admin.org_content.preview")}
                    </Button>
                  ) : null}
                  <Button
                    type="button"
                    variant="destructive"
                    size="sm"
                    onClick={() => {
                      setRemoving(file);
                    }}
                  >
                    {t("common.admin.org_content.remove")}
                  </Button>
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <LoadMore
        hasMore={query.hasNextPage}
        isLoading={query.isFetchingNextPage}
        onClick={() => {
          void query.fetchNextPage();
        }}
      />

      {removing ? (
        <Dialog
          open
          onOpenChange={(open) => {
            if (!open) {
              setRemoving(null);
              setReason("");
              remove.reset();
            }
          }}
        >
          <DialogContent data-testid="remove-file-dialog">
            <DialogHeader>
              <DialogTitle>
                {t("common.admin.org_content.remove_title", {
                  filename: removing.filename,
                })}
              </DialogTitle>
              <DialogDescription>
                {t("common.admin.org_content.remove_description")}
              </DialogDescription>
            </DialogHeader>
            <form
              className="space-y-4"
              noValidate
              onSubmit={(event) => {
                event.preventDefault();
                if (reasonValid) remove.mutate(removing);
              }}
            >
              {remove.isError ? (
                <Alert variant="destructive">
                  <AlertDescription>
                    {getErrorMessage(
                      remove.error,
                      "common.admin.org_content.error_remove",
                    )}
                  </AlertDescription>
                </Alert>
              ) : null}
              <div className="space-y-2">
                <Label htmlFor="remove-file-reason">
                  {t("common.admin.org_content.reason_label")}
                </Label>
                <Input
                  id="remove-file-reason"
                  value={reason}
                  minLength={MIN_REASON_LENGTH}
                  required
                  onChange={(event) => {
                    setReason(event.target.value);
                  }}
                />
                <p className="text-xs text-muted-foreground">
                  {t("common.admin.org_content.reason_hint")}
                </p>
              </div>
              <div className="flex justify-end gap-2">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => {
                    setRemoving(null);
                    setReason("");
                  }}
                >
                  {t("common.admin.org_content.cancel")}
                </Button>
                <Button
                  type="submit"
                  variant="destructive"
                  disabled={!reasonValid || remove.isPending}
                >
                  {t("common.admin.org_content.remove")}
                </Button>
              </div>
            </form>
          </DialogContent>
        </Dialog>
      ) : null}
    </div>
  );
}
