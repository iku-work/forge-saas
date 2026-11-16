import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import {
  ADMIN_ACTIONS_NEEDING_REASON,
  moderateAdminUser,
} from "@forge/api-client";
import type { AdminModerationAction, AdminUserDetail } from "@forge/api-client";
import { useModules } from "@forge/hooks";
import {
  Alert,
  AlertDescription,
  Button,
  ConfirmDialog,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  SettingsSection,
  StatusPill,
  toast,
} from "@forge/ui";

import { getErrorMessage } from "../auth/getErrorMessage";

const MIN_REASON_LENGTH = 10;

// Translation keys can't hold the hyphens the URL segments use.
const KEY: Record<AdminModerationAction, string> = {
  suspend: "suspend",
  unsuspend: "unsuspend",
  ban: "ban",
  unban: "unban",
  "sign-out-everywhere": "sign_out",
  "verify-email": "verify_email",
  delete: "delete",
};

const DESTRUCTIVE: ReadonlySet<AdminModerationAction> = new Set([
  "suspend",
  "ban",
  "delete",
]);

function availableActions(
  detail: AdminUserDetail,
  canDelete: boolean,
): AdminModerationAction[] {
  const actions: AdminModerationAction[] = [];
  if (detail.email_verified_at === null) actions.push("verify-email");
  // The backend refuses the rest on a superadmin; don't offer them.
  if (detail.is_superadmin) return actions;
  actions.push("sign-out-everywhere");
  if (!detail.banned) {
    actions.push(detail.suspended_at === null ? "suspend" : "unsuspend");
  }
  actions.push(detail.banned ? "unban" : "ban");
  if (canDelete) actions.push("delete");
  return actions;
}

/**
 * Suspend, ban, sign out, verify, delete: what a superadmin can do to one
 * account. Each needs a confirmation, and the three that take something
 * away also need a reason for the audit log.
 */
export function UserModerationSection({ detail }: { detail: AdminUserDetail }) {
  const { t } = useTranslation();
  const modules = useModules();
  const queryClient = useQueryClient();
  const [action, setAction] = useState<AdminModerationAction | null>(null);
  const [reason, setReason] = useState("");

  const mutation = useMutation({
    mutationFn: async (chosen: AdminModerationAction) => {
      await moderateAdminUser(detail.id, chosen, reason.trim());
    },
    onSuccess: async (_data, chosen) => {
      toast(t(`common.admin.moderation.${KEY[chosen]}.done`));
      setAction(null);
      setReason("");
      await queryClient.invalidateQueries({
        queryKey: ["admin", "users", "detail", detail.id],
      });
    },
  });

  const close = (open: boolean): void => {
    if (!open) {
      setAction(null);
      setReason("");
      mutation.reset();
    }
  };

  const actions = availableActions(detail, modules.gdpr);
  const needsReason =
    action !== null && ADMIN_ACTIONS_NEEDING_REASON.has(action);
  const reasonValid = reason.trim().length >= MIN_REASON_LENGTH;

  return (
    <SettingsSection
      title={t("common.admin.moderation.title")}
      description={t("common.admin.moderation.description")}
    >
      <div className="space-y-4" data-testid="user-moderation">
        <div className="flex flex-wrap gap-2">
          {detail.suspended_at !== null ? (
            <StatusPill tone="danger">
              {t("common.admin.moderation.status_suspended")}
            </StatusPill>
          ) : null}
          {detail.banned ? (
            <StatusPill tone="danger">
              {t("common.admin.moderation.status_banned")}
            </StatusPill>
          ) : null}
          {detail.email_verified_at === null ? (
            <StatusPill tone="pending">
              {t("common.admin.moderation.status_unverified")}
            </StatusPill>
          ) : null}
        </div>
        {detail.is_superadmin ? (
          <p className="text-sm text-muted-foreground">
            {t("common.admin.moderation.protected_note")}
          </p>
        ) : null}
        <div className="flex flex-wrap gap-2">
          {actions.map((candidate) => (
            <Button
              key={candidate}
              type="button"
              variant={DESTRUCTIVE.has(candidate) ? "destructive" : "outline"}
              onClick={() => {
                setAction(candidate);
              }}
            >
              {t(`common.admin.moderation.${KEY[candidate]}.label`)}
            </Button>
          ))}
        </div>
      </div>

      {action !== null && !needsReason ? (
        <ConfirmDialog
          open
          onOpenChange={close}
          title={t(`common.admin.moderation.${KEY[action]}.title`, {
            email: detail.email,
          })}
          description={
            mutation.isError
              ? getErrorMessage(mutation.error, "common.admin.moderation.error")
              : t(`common.admin.moderation.${KEY[action]}.description`)
          }
          confirmLabel={t(`common.admin.moderation.${KEY[action]}.label`)}
          isPending={mutation.isPending}
          onConfirm={() => {
            mutation.mutate(action);
          }}
        />
      ) : null}

      {action !== null && needsReason ? (
        <Dialog open onOpenChange={close}>
          <DialogContent data-testid="moderation-reason-dialog">
            <DialogHeader>
              <DialogTitle>
                {t(`common.admin.moderation.${KEY[action]}.title`, {
                  email: detail.email,
                })}
              </DialogTitle>
              <DialogDescription>
                {t(`common.admin.moderation.${KEY[action]}.description`)}
              </DialogDescription>
            </DialogHeader>
            <form
              className="space-y-4"
              noValidate
              onSubmit={(event) => {
                event.preventDefault();
                if (reasonValid) mutation.mutate(action);
              }}
            >
              {mutation.isError ? (
                <Alert variant="destructive">
                  <AlertDescription>
                    {getErrorMessage(
                      mutation.error,
                      "common.admin.moderation.error",
                    )}
                  </AlertDescription>
                </Alert>
              ) : null}
              <div className="space-y-2">
                <Label htmlFor="moderation-reason">
                  {t("common.admin.moderation.reason_label")}
                </Label>
                <Input
                  id="moderation-reason"
                  value={reason}
                  minLength={MIN_REASON_LENGTH}
                  required
                  onChange={(event) => {
                    setReason(event.target.value);
                  }}
                />
                <p className="text-xs text-muted-foreground">
                  {t("common.admin.moderation.reason_hint")}
                </p>
              </div>
              <div className="flex justify-end gap-2">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => {
                    close(false);
                  }}
                >
                  {t("common.admin.moderation.cancel")}
                </Button>
                <Button
                  type="submit"
                  variant="destructive"
                  disabled={!reasonValid || mutation.isPending}
                >
                  {t(`common.admin.moderation.${KEY[action]}.label`)}
                </Button>
              </div>
            </form>
          </DialogContent>
        </Dialog>
      ) : null}
    </SettingsSection>
  );
}
