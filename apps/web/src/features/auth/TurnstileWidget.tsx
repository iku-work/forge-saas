import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@forge/ui";

interface TurnstileApi {
  render: (
    element: HTMLElement,
    options: {
      sitekey: string;
      language: string;
      callback: (token: string) => void;
      "expired-callback": () => void;
      "error-callback": () => void;
    },
  ) => string;
  remove: (widgetId: string) => void;
}

declare global {
  interface Window {
    turnstile?: TurnstileApi;
  }
}

// Allowed by the app's CSP (script-src and frame-src name this host), see
// Caddyfile.prod. render=explicit: the widget appears where this component
// puts it, not wherever the script finds a matching class.
const SCRIPT_SRC =
  "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

let scriptPromise: Promise<TurnstileApi> | null = null;

function loadTurnstile(): Promise<TurnstileApi> {
  if (window.turnstile) return Promise.resolve(window.turnstile);
  scriptPromise ??= new Promise<TurnstileApi>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = SCRIPT_SRC;
    script.async = true;
    script.onload = () => {
      if (window.turnstile) resolve(window.turnstile);
      else reject(new Error("turnstile missing after load"));
    };
    script.onerror = () => {
      // Let the next mount try again rather than caching the failure.
      scriptPromise = null;
      reject(new Error("turnstile script failed to load"));
    };
    document.head.appendChild(script);
  });
  return scriptPromise;
}

/**
 * Cloudflare Turnstile's check. Calls `onToken` with a token once the check
 * passes, and with null when it expires or errors. A token is single-use:
 * after a submit, remount this (change its `key`) for a fresh one.
 */
export function TurnstileWidget({
  siteKey,
  onToken,
}: {
  siteKey: string;
  onToken: (token: string | null) => void;
}) {
  const { t, i18n } = useTranslation();
  const container = useRef<HTMLDivElement>(null);
  const onTokenRef = useRef(onToken);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    onTokenRef.current = onToken;
  }, [onToken]);

  useEffect(() => {
    let widgetId: string | null = null;
    let cancelled = false;
    loadTurnstile()
      .then((turnstile) => {
        if (cancelled || !container.current) return;
        widgetId = turnstile.render(container.current, {
          sitekey: siteKey,
          language: i18n.language,
          callback: (token) => {
            onTokenRef.current(token);
          },
          "expired-callback": () => {
            onTokenRef.current(null);
          },
          "error-callback": () => {
            onTokenRef.current(null);
          },
        });
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
      if (widgetId !== null) window.turnstile?.remove(widgetId);
    };
  }, [siteKey, i18n.language]);

  if (failed) {
    return (
      <Alert variant="destructive" data-testid="turnstile-unavailable">
        <AlertDescription>{t("auth.captcha.unavailable")}</AlertDescription>
      </Alert>
    );
  }
  return (
    <div
      ref={container}
      role="group"
      aria-label={t("auth.captcha.label")}
      data-testid="turnstile"
      className="min-h-[65px]"
    />
  );
}
