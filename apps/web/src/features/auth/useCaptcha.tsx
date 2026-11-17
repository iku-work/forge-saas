import { useCallback, useState } from "react";
import type { ReactNode } from "react";

import { useTurnstileSiteKey } from "@forge/hooks";

import { TurnstileWidget } from "./TurnstileWidget";

export interface Captcha {
  /** The widget to render above the submit button, or null when off. */
  widget: ReactNode;
  /** Turnstile's token for the next request, or null. */
  token: string | null;
  /** False while the check is on and not yet passed. */
  ready: boolean;
  /** After a failed submit: the token is spent, so show a fresh check. */
  reset: () => void;
}

/** The check for one form: sign-up, magic link or forgot password. */
export function useCaptcha(): Captcha {
  const siteKey = useTurnstileSiteKey();
  const [token, setToken] = useState<string | null>(null);
  const [round, setRound] = useState(0);
  const reset = useCallback(() => {
    setToken(null);
    setRound((current) => current + 1);
  }, []);
  return {
    widget: siteKey ? (
      <TurnstileWidget key={round} siteKey={siteKey} onToken={setToken} />
    ) : null,
    token,
    ready: siteKey === null || token !== null,
    reset,
  };
}
