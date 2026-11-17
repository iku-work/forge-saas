import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { MessageSquarePlus } from "lucide-react";
import { useTranslation } from "react-i18next";

import { sendFeedbackApiV1FeedbackPost } from "@forge/api-client";
import { useModules, useOrg } from "@forge/hooks";
import {
  Alert,
  AlertDescription,
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  Label,
  Select,
  toast,
} from "@forge/ui";

import { getErrorMessage } from "../auth/getErrorMessage";

const CATEGORIES = ["bug", "idea", "other"] as const;
type Category = (typeof CATEGORIES)[number];
const MAX_LENGTH = 5000;

/**
 * "Send feedback" in the sidebar footer: a short form whose message lands in
 * the admin Feedback inbox, with the page it was sent from and the current
 * org attached. Only shown where there is an admin area to read it.
 */
export function FeedbackButton() {
  const { t } = useTranslation();
  const modules = useModules();
  const { org } = useOrg();
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState<Category>("bug");
  const [message, setMessage] = useState("");

  const send = useMutation({
    mutationFn: async () => {
      await sendFeedbackApiV1FeedbackPost({
        body: {
          category,
          message,
          page: window.location.pathname,
          org_id: org?.id ?? null,
        },
        throwOnError: true,
      });
    },
    onSuccess: () => {
      toast(t("common.feedback.sent"));
      setOpen(false);
      setMessage("");
      setCategory("bug");
    },
  });

  if (!modules.admin) {
    return null;
  }

  const valid = message.trim().length > 0;

  return (
    <>
      <button
        type="button"
        onClick={() => {
          send.reset();
          setOpen(true);
        }}
        // Same look as the support link above it.
        className="flex items-center gap-2 rounded-md px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground group-data-[collapsed]/shell:size-10 group-data-[collapsed]/shell:justify-center group-data-[collapsed]/shell:px-0"
        data-testid="feedback-button"
      >
        <MessageSquarePlus className="size-4 shrink-0" aria-hidden="true" />
        <span className="group-data-[collapsed]/shell:sr-only">
          {t("common.feedback.open")}
        </span>
      </button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent data-testid="feedback-dialog">
          <DialogHeader>
            <DialogTitle>{t("common.feedback.title")}</DialogTitle>
            <DialogDescription>
              {t("common.feedback.description")}
            </DialogDescription>
          </DialogHeader>
          <form
            className="space-y-4"
            noValidate
            onSubmit={(event) => {
              event.preventDefault();
              if (valid) send.mutate();
            }}
          >
            {send.isError ? (
              <Alert variant="destructive">
                <AlertDescription>
                  {getErrorMessage(send.error, "common.feedback.error")}
                </AlertDescription>
              </Alert>
            ) : null}
            <div className="space-y-2">
              <Label htmlFor="feedback-category">
                {t("common.feedback.category_label")}
              </Label>
              <Select
                id="feedback-category"
                value={category}
                onChange={(event) => {
                  setCategory(event.target.value as Category);
                }}
              >
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {t(`common.feedback.category.${c}`)}
                  </option>
                ))}
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="feedback-message">
                {t("common.feedback.message_label")}
              </Label>
              <textarea
                id="feedback-message"
                value={message}
                maxLength={MAX_LENGTH}
                required
                rows={6}
                onChange={(event) => {
                  setMessage(event.target.value);
                }}
                className="w-full rounded-md border border-input bg-card p-3 text-sm"
              />
              <p className="text-xs text-muted-foreground">
                {t("common.feedback.context_hint")}
              </p>
            </div>
            <div className="flex justify-end gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  setOpen(false);
                }}
              >
                {t("common.feedback.cancel")}
              </Button>
              <Button type="submit" disabled={!valid || send.isPending}>
                {t("common.feedback.send")}
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}
