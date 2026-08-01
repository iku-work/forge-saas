import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { impersonateAdminUser } from "@forge/api-client";
import { useAuth } from "@forge/hooks";
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

import { useRouteLocale } from "../../lib/useRouteLocale";
import { getErrorMessage } from "../auth/getErrorMessage";

interface ImpersonateModalProps {
  userId: string;
  email: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const MAX_DURATION_MINUTES = 60;

export function ImpersonateModal({
  userId,
  email,
  open,
  onOpenChange,
}: ImpersonateModalProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const locale = useRouteLocale();
  const { loadImpersonatedSession } = useAuth();
  const [reason, setReason] = useState("");
  const [duration, setDuration] = useState("30");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: async () => {
      const { data: envelope } = await impersonateAdminUser(userId, {
        reason,
        duration_minutes: Number(duration),
      });
      return envelope.data;
    },
  });

  const handleSubmit = async (event: React.FormEvent): Promise<void> => {
    event.preventDefault();
    setError(null);
    try {
      const result = await mutation.mutateAsync();
      if (!result) {
        throw new Error("Impersonation response was empty.");
      }
      loadImpersonatedSession(result.access_token, result.user);
      onOpenChange(false);
      await navigate({ to: "/$locale", params: { locale } });
    } catch (err) {
      setError(getErrorMessage(err, "common.admin.impersonate_modal.error_default"));
    }
  };

  const durationValue = Number(duration);
  const isValid =
    reason.trim().length >= 10 &&
    Number.isInteger(durationValue) &&
    durationValue > 0 &&
    durationValue <= MAX_DURATION_MINUTES;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("common.admin.impersonate_modal.title", { email })}</DialogTitle>
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
            <Label htmlFor="impersonate-reason">
              {t("common.admin.impersonate_modal.reason_label")}
            </Label>
            <Input
              id="impersonate-reason"
              value={reason}
              onChange={(event) => {
                setReason(event.target.value);
              }}
              required
              minLength={10}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="impersonate-duration">
              {t("common.admin.impersonate_modal.duration_label")}
            </Label>
            <Input
              id="impersonate-duration"
              type="number"
              min={1}
              max={MAX_DURATION_MINUTES}
              value={duration}
              onChange={(event) => {
                setDuration(event.target.value);
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
              {t("common.admin.impersonate_modal.cancel")}
            </Button>
            <Button type="submit" disabled={mutation.isPending || !isValid}>
              {mutation.isPending
                ? t("common.admin.impersonate_modal.submit_pending")
                : t("common.admin.impersonate_modal.submit")}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
