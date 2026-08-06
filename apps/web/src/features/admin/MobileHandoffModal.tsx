import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import {
  createAdminMobileHandoff,
  impersonateAdminUser,
} from "@forge/api-client";
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
import { QRCodeSVG } from "qrcode.react";

import { getErrorMessage } from "../auth/getErrorMessage";

interface MobileHandoffModalProps {
  userId: string;
  email: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const MAX_DURATION_MINUTES = 60;

interface Handoff {
  deepLink: string;
  expiresAt: number;
}

/**
 * T-261: starts an impersonation session and hands it straight to the
 * operator's phone, without this browser ever adopting it.
 *
 * That last part is the difference from ImpersonateModal next door, which
 * calls the same first endpoint: there, `loadImpersonatedSession` swaps the
 * web session over and navigates into the app as the user. Here the minted
 * token is used for exactly one call — the handoff — and then dropped on the
 * floor. The admin stays themselves on this screen, which is what makes the
 * two flows usable back to back (reproduce on the phone, keep reading the
 * audit feed on the laptop) and keeps a superadmin from being left
 * impersonating in a tab they forgot about.
 */
export function MobileHandoffModal({
  userId,
  email,
  open,
  onOpenChange,
}: MobileHandoffModalProps) {
  const { t } = useTranslation();
  const [reason, setReason] = useState("");
  const [duration, setDuration] = useState("30");
  const [error, setError] = useState<string | null>(null);
  const [handoff, setHandoff] = useState<Handoff | null>(null);

  const mutation = useMutation({
    mutationFn: async (): Promise<Handoff> => {
      const { data: session } = await impersonateAdminUser(userId, {
        reason,
        duration_minutes: Number(duration),
      });
      if (!session.data) {
        throw new Error("Impersonation response was empty.");
      }
      const { data: link } = await createAdminMobileHandoff(
        session.data.access_token,
      );
      if (!link.data) {
        throw new Error("Handoff response was empty.");
      }
      return {
        deepLink: link.data.deep_link,
        expiresAt: new Date(link.data.expires_at).getTime(),
      };
    },
  });

  const handleSubmit = async (event: React.FormEvent): Promise<void> => {
    event.preventDefault();
    setError(null);
    try {
      setHandoff(await mutation.mutateAsync());
    } catch (err) {
      setError(
        getErrorMessage(err, "common.admin.mobile_handoff.error_default"),
      );
    }
  };

  const handleOpenChange = (next: boolean): void => {
    // A link left on screen after the dialog closes is a link nobody is
    // watching; the two-minute server-side expiry is the real control, but
    // there is no reason to keep rendering it either.
    if (!next) {
      setHandoff(null);
      setError(null);
    }
    onOpenChange(next);
  };

  const durationValue = Number(duration);
  const isValid =
    reason.trim().length >= 10 &&
    Number.isInteger(durationValue) &&
    durationValue > 0 &&
    durationValue <= MAX_DURATION_MINUTES;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {t("common.admin.mobile_handoff.title", { email })}
          </DialogTitle>
        </DialogHeader>
        {error ? (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        {handoff === null ? (
          <form
            className="space-y-4"
            onSubmit={(event) => {
              void handleSubmit(event);
            }}
            noValidate
          >
            <p className="text-sm text-muted-foreground">
              {t("common.admin.mobile_handoff.description")}
            </p>
            <div className="space-y-2">
              <Label htmlFor="handoff-reason">
                {t("common.admin.mobile_handoff.reason_label")}
              </Label>
              <Input
                id="handoff-reason"
                value={reason}
                onChange={(event) => {
                  setReason(event.target.value);
                }}
                required
                minLength={10}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="handoff-duration">
                {t("common.admin.mobile_handoff.duration_label")}
              </Label>
              <Input
                id="handoff-duration"
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
                  handleOpenChange(false);
                }}
              >
                {t("common.admin.mobile_handoff.cancel")}
              </Button>
              <Button type="submit" disabled={mutation.isPending || !isValid}>
                {mutation.isPending
                  ? t("common.admin.mobile_handoff.submit_pending")
                  : t("common.admin.mobile_handoff.submit")}
              </Button>
            </div>
          </form>
        ) : (
          <div className="space-y-4">
            <HandoffLink handoff={handoff} />
            <div className="flex justify-end gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  // Back to the form. Generating another link means starting
                  // another impersonation session, which is why this is a
                  // deliberate step rather than an automatic refresh.
                  setHandoff(null);
                }}
              >
                {t("common.admin.mobile_handoff.start_over")}
              </Button>
              <Button
                type="button"
                onClick={() => {
                  handleOpenChange(false);
                }}
              >
                {t("common.admin.mobile_handoff.done")}
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

/**
 * The live half of the dialog: the QR code, the copyable link, and the
 * countdown — or, once the two minutes are up, the notice that says so.
 *
 * A separate component so it mounts with a deadline already in hand, which is
 * what lets useSecondsUntil below start from the right number instead of
 * catching up a second later (which would flash the expired notice at a link
 * that is perfectly alive).
 */
function HandoffLink({ handoff }: { handoff: Handoff }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const secondsLeft = useSecondsUntil(handoff.expiresAt);

  if (secondsLeft <= 0) {
    return (
      <Alert variant="destructive">
        <AlertDescription>
          {t("common.admin.mobile_handoff.expired")}
        </AlertDescription>
      </Alert>
    );
  }

  return (
    <>
      <p className="text-sm text-muted-foreground">
        {t("common.admin.mobile_handoff.scan_instruction")}
      </p>
      <div className="flex justify-center rounded-md bg-white p-4">
        <QRCodeSVG
          value={handoff.deepLink}
          size={192}
          // The deep link is the accessible content here; without this the QR
          // is an unlabelled <svg> to a screen reader, and the copyable text
          // below is the only way in.
          title={t("common.admin.mobile_handoff.qr_title")}
        />
      </div>
      <div className="flex items-start gap-2">
        <p
          className="min-w-0 flex-1 break-all rounded-md bg-muted p-2 font-mono text-xs"
          data-testid="handoff-deep-link"
        >
          {handoff.deepLink}
        </p>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => {
            // A custom-scheme URL is awkward to select by hand and easy to
            // truncate; getting one character wrong wastes the whole
            // two-minute window. Writing to the clipboard rejects outside a
            // secure context (plain http on a non-localhost origin), which is
            // swallowed here -- the text alongside stays selectable, which is
            // the fallback.
            void navigator.clipboard
              .writeText(handoff.deepLink)
              .then(() => {
                setCopied(true);
              })
              .catch(() => undefined);
          }}
        >
          {copied
            ? t("common.admin.mobile_handoff.copied")
            : t("common.admin.mobile_handoff.copy")}
        </Button>
      </div>
      <p aria-live="polite" className="text-sm">
        {t("common.admin.mobile_handoff.expires_in", { seconds: secondsLeft })}
      </p>
    </>
  );
}

// Module scope, not inside the hook: reading the clock is impure and render
// has to stay pure (react-hooks/purity). Every read happens in a lazy
// initializer or a timer callback.
function secondsUntil(deadline: number): number {
  return Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
}

/**
 * Whole seconds until `deadline` (an epoch ms value), never negative.
 *
 * Each tick re-reads the clock rather than subtracting one: a background tab
 * throttles setInterval to once a minute or worse, and a decrementing counter
 * would drift into claiming a dead link is still good.
 */
function useSecondsUntil(deadline: number): number {
  const [seconds, setSeconds] = useState(() => secondsUntil(deadline));

  useEffect(() => {
    const timer = setInterval(() => {
      setSeconds(secondsUntil(deadline));
    }, 1000);
    return () => {
      clearInterval(timer);
    };
  }, [deadline]);

  return seconds;
}
