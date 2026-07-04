import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { issueAdminOrgCompCredit } from "@forge/api-client";
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

interface CompCreditModalProps {
  orgId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDone: () => void;
}

export function CompCreditModal({
  orgId,
  open,
  onOpenChange,
  onDone,
}: CompCreditModalProps) {
  const { t } = useTranslation();
  const [amountCents, setAmountCents] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: async () => {
      await issueAdminOrgCompCredit(orgId, Number(amountCents), reason);
    },
  });

  const handleSubmit = async (event: React.FormEvent): Promise<void> => {
    event.preventDefault();
    setError(null);
    try {
      await mutation.mutateAsync();
      onDone();
    } catch (err) {
      setError(getErrorMessage(err, "common.admin.org_detail.error_action_default"));
    }
  };

  const amountValue = Number(amountCents);
  const isValid = Number.isInteger(amountValue) && amountValue > 0 && reason.trim().length > 0;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("common.admin.comp_credit_modal.title")}</DialogTitle>
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
            <Label htmlFor="comp-amount">{t("common.admin.comp_credit_modal.amount_label")}</Label>
            <Input
              id="comp-amount"
              type="number"
              min={1}
              value={amountCents}
              onChange={(event) => {
                setAmountCents(event.target.value);
              }}
              required
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="comp-reason">{t("common.admin.comp_credit_modal.reason_label")}</Label>
            <Input
              id="comp-reason"
              value={reason}
              onChange={(event) => {
                setReason(event.target.value);
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
              {t("common.admin.comp_credit_modal.cancel")}
            </Button>
            <Button type="submit" disabled={mutation.isPending || !isValid}>
              {mutation.isPending
                ? t("common.admin.comp_credit_modal.submit_pending")
                : t("common.admin.comp_credit_modal.submit")}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
