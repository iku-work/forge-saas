import { useTranslation } from "react-i18next";

import { Button } from "./button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "./dialog";

export interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  /** The button that does the thing. Action-oriented ("Delete file"), never
   *  "OK" -- §5.8 of the design plan, and the label is the last chance to say
   *  what is about to happen. */
  confirmLabel: string;
  onConfirm: () => void;
  destructive?: boolean;
  isPending?: boolean;
}

/**
 * A confirmation, for the actions that genuinely warrant one (F-009).
 *
 * Most actions do not: an undo toast interrupts nothing and is safer, because
 * people click through confirmations without reading them. This is for the
 * rest -- the irreversible, the expensive, and the ones that affect other
 * people.
 *
 * It replaces `window.confirm`, which this app used for deleting a file. That
 * was wrong on four counts: it blocks the main thread, it cannot be styled or
 * themed (a white OS dialog over a dark app), its buttons say "OK"/"Cancel"
 * rather than what they do, and it is not translatable -- the browser picks
 * its own language, not the user's.
 *
 * Fitts's law shapes the footer: the confirm button is last in the DOM, which
 * in a right-aligned footer puts it at the end of the row -- away from where
 * the pointer lands after opening the dialog, and it is never the element
 * that gets initial focus.
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  onConfirm,
  destructive = false,
  isPending = false,
}: ConfirmDialogProps) {
  const { t } = useTranslation();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="confirm-dialog">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              onOpenChange(false);
            }}
            data-testid="confirm-dialog-cancel"
          >
            {t("common.button.cancel")}
          </Button>
          <Button
            type="button"
            variant={destructive ? "destructive" : "default"}
            disabled={isPending}
            onClick={onConfirm}
            data-testid="confirm-dialog-confirm"
          >
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
