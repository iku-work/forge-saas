import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { setAdminOrgPlan } from "@forge/api-client";
import {
  Alert,
  AlertDescription,
  Button,
  Checkbox,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  Select,
} from "@forge/ui";

import { getErrorMessage } from "../auth/getErrorMessage";

interface PlanOverrideModalProps {
  orgId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDone: () => void;
}

export function PlanOverrideModal({
  orgId,
  open,
  onOpenChange,
  onDone,
}: PlanOverrideModalProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [planSlug, setPlanSlug] = useState("");
  const [billingInterval, setBillingInterval] = useState<
    "" | "monthly" | "yearly"
  >("");
  const [freeOverride, setFreeOverride] = useState(false);
  const [expiresAt, setExpiresAt] = useState("");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: async () => {
      await setAdminOrgPlan(orgId, {
        plan_slug: planSlug,
        billing_interval: billingInterval || null,
        free_override: freeOverride,
        expires_at: expiresAt ? new Date(expiresAt).toISOString() : null,
      });
    },
  });

  const handleSubmit = async (event: React.FormEvent): Promise<void> => {
    event.preventDefault();
    setError(null);
    try {
      await mutation.mutateAsync();
      await queryClient.invalidateQueries({
        queryKey: ["admin", "orgs", "detail", orgId],
      });
      onDone();
    } catch (err) {
      setError(
        getErrorMessage(err, "common.admin.org_detail.error_action_default"),
      );
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {t("common.admin.plan_override_modal.title")}
          </DialogTitle>
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
            <Label htmlFor="plan-slug">
              {t("common.admin.plan_override_modal.plan_slug_label")}
            </Label>
            <Input
              id="plan-slug"
              value={planSlug}
              onChange={(event) => {
                setPlanSlug(event.target.value);
              }}
              required
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="billing-interval">
              {t("common.admin.plan_override_modal.billing_interval_label")}
            </Label>
            <Select
              id="billing-interval"
              value={billingInterval}
              onChange={(event) => {
                setBillingInterval(
                  event.target.value as "" | "monthly" | "yearly",
                );
              }}
              className="w-full"
            >
              <option value="">
                {t("common.admin.plan_override_modal.billing_interval_none")}
              </option>
              <option value="monthly">
                {t("common.admin.plan_override_modal.billing_interval_monthly")}
              </option>
              <option value="yearly">
                {t("common.admin.plan_override_modal.billing_interval_yearly")}
              </option>
            </Select>
          </div>
          <div className="flex items-center gap-2">
            <Checkbox
              id="free-override"
              checked={freeOverride}
              onCheckedChange={(checked) => {
                setFreeOverride(checked === true);
              }}
            />
            <Label htmlFor="free-override">
              {t("common.admin.plan_override_modal.free_override_label")}
            </Label>
          </div>
          <div className="space-y-2">
            <Label htmlFor="expires-at">
              {t("common.admin.plan_override_modal.expires_at_label")}
            </Label>
            <Input
              id="expires-at"
              type="date"
              value={expiresAt}
              onChange={(event) => {
                setExpiresAt(event.target.value);
              }}
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
              {t("common.admin.plan_override_modal.cancel")}
            </Button>
            <Button type="submit" disabled={mutation.isPending || !planSlug}>
              {mutation.isPending
                ? t("common.admin.plan_override_modal.submit_pending")
                : t("common.admin.plan_override_modal.submit")}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
