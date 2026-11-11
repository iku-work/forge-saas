import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { setMemberPermissionsApiV1OrgsOrgIdMembersMembershipIdPermissionsPut } from "@forge/api-client";
import type { MembershipPublic } from "@forge/api-client";
import { useOrg } from "@forge/hooks";
import { GRANTABLE_PERMISSIONS, type Permission } from "@forge/types";
import {
  Alert,
  AlertDescription,
  Button,
  Checkbox,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Label,
} from "@forge/ui";

import { getErrorMessage } from "../auth/getErrorMessage";

/** "files:write" -> "files_write", the key segment for its label and hint. */
function keyFor(permission: Permission): string {
  return permission.replace(":", "_");
}

/**
 * What one member has been granted on top of their role.
 *
 * Only someone who holds a permission can give it or take it away, so the
 * ones the person editing lacks are shown but disabled (the API refuses
 * them too). Mounted per member (the caller keys it), so the checkboxes
 * start from that member's grants without an effect copying props into
 * state.
 */
export function MemberPermissionsDialog({
  orgId,
  member,
  name,
  onClose,
  onSaved,
}: {
  orgId: string;
  member: MembershipPublic;
  name: string;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const { permissions: own } = useOrg();
  const [granted, setGranted] = useState<ReadonlySet<string>>(
    () => new Set(member.granted_permissions),
  );
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: async () => {
      await setMemberPermissionsApiV1OrgsOrgIdMembersMembershipIdPermissionsPut(
        {
          path: { org_id: orgId, membership_id: member.id },
          body: {
            granted_permissions: GRANTABLE_PERMISSIONS.filter((p) =>
              granted.has(p),
            ),
          },
          throwOnError: true,
        },
      );
    },
  });

  const toggle = (permission: Permission, on: boolean): void => {
    setGranted((current) => {
      const next = new Set(current);
      if (on) {
        next.add(permission);
      } else {
        next.delete(permission);
      }
      return next;
    });
  };

  const onSave = async (): Promise<void> => {
    setError(null);
    try {
      await save.mutateAsync();
      await onSaved();
      onClose();
    } catch (caught) {
      setError(getErrorMessage(caught, "tenancy.members.permissions.error"));
    }
  };

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) {
          onClose();
        }
      }}
    >
      <DialogContent data-testid="member-permissions-dialog">
        <DialogHeader>
          <DialogTitle>
            {t("tenancy.members.permissions.title", { name })}
          </DialogTitle>
          <DialogDescription>
            {t("tenancy.members.permissions.description")}
          </DialogDescription>
        </DialogHeader>
        {error ? (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        <ul className="space-y-4">
          {GRANTABLE_PERMISSIONS.map((permission) => {
            const id = `grant-${keyFor(permission)}`;
            return (
              <li key={permission} className="flex items-start gap-3">
                <Checkbox
                  id={id}
                  className="mt-0.5"
                  checked={granted.has(permission)}
                  disabled={!own.has(permission)}
                  onCheckedChange={(checked) => {
                    toggle(permission, checked === true);
                  }}
                />
                <div className="leading-tight">
                  <Label htmlFor={id}>
                    {t(`tenancy.members.permissions.${keyFor(permission)}`)}
                  </Label>
                  <p className="mt-1 text-[0.8125rem] text-muted-foreground">
                    {t(
                      `tenancy.members.permissions.${keyFor(permission)}_hint`,
                    )}
                  </p>
                </div>
              </li>
            );
          })}
        </ul>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            {t("common.button.cancel")}
          </Button>
          <Button
            type="button"
            disabled={save.isPending}
            onClick={() => {
              void onSave();
            }}
            data-testid="member-permissions-save"
          >
            {save.isPending
              ? t("common.status.saving")
              : t("common.button.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
