import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { extendAdminOrgTrial } from "@forge/api-client";
import {
  Alert,
  AlertDescription,
  Button,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
} from "@forge/ui";

import { getErrorMessage } from "../auth/getErrorMessage";

interface ExtendTrialModalProps {
  orgId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDone: () => void;
}

export function ExtendTrialModal({
  orgId,
  open,
  onOpenChange,
  onDone,
}: ExtendTrialModalProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [days, setDays] = useState("7");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: async () => {
      await extendAdminOrgTrial(orgId, Number(days));
    },
  });

  const handleSubmit = async (event: React.FormEvent): Promise<void> => {
    event.preventDefault();
    setError(null);
    try {
      await mutation.mutateAsync();
      await queryClient.invalidateQueries({ queryKey: ["admin", "orgs", "detail", orgId] });
      onDone();
    } catch (err) {
      setError(getErrorMessage(err, "common.admin.org_detail.error_action_default"));
    }
  };

  const daysValue = Number(days);
  const isValid = Number.isInteger(daysValue) && daysValue > 0;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("common.admin.extend_trial_modal.title")}</DialogTitle>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            void handleSubmit(event);
          }}
          noValidate
        >
          {error ? (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          <div className="space-y-2">
            <Label htmlFor="extend-days">{t("common.admin.extend_trial_modal.days_label")}</Label>
            <Input
              id="extend-days"
              type="number"
              min={1}
              value={days}
              onChange={(event) => {
                setDays(event.target.value);
              }}
              required
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                onOpenChange(false);
              }}
            >
              {t("common.admin.extend_trial_modal.cancel")}
            </Button>
            <Button type="submit" disabled={mutation.isPending || !isValid}>
              {mutation.isPending
                ? t("common.admin.extend_trial_modal.submit_pending")
                : t("common.admin.extend_trial_modal.submit")}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
