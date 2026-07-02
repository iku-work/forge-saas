/**
 * Mirrors packages/hooks's useLocale.ts (T-069), reimplemented here because
 * that hook calls @forge/api-client's default singleton client, which is
 * wired for the browser (resolves its base URL from Vite's
 * `import.meta.env` and authenticates via a cookie) and doesn't exist in
 * this runtime -- same reason ./config.ts and ./auth.ts each reimplement
 * their own web counterpart instead of importing it directly. Same
 * "subscribe to i18next, bootstrap supported locales from /config once,
 * persist a pick via PATCH /api/v1/auth/me" semantics.
 */
import { useCallback, useEffect, useSyncExternalStore } from "react";

import {
  getConfigApiV1ConfigGet,
  updateMeApiV1AuthMePatch,
} from "@forge/api-client";
import { DEFAULT_LOCALE } from "@forge/i18n";
import i18next from "i18next";

import { client } from "./api";
import { pinLocale } from "./i18n";

export interface UseLocaleResult {
  locale: string;
  supportedLocales: string[];
  setLocale: (locale: string) => Promise<void>;
}

// Deny-by-default mirrors config.ts's DENY_BY_DEFAULT: until /config
// resolves, only DEFAULT_LOCALE is offered rather than @forge/i18n's full
// package-level SUPPORTED_LOCALES, since a product may ship a narrower
// subset.
let supportedLocales: string[] = [DEFAULT_LOCALE];
const configListeners = new Set<() => void>();

function setSupportedLocales(next: string[]): void {
  supportedLocales = next;
  for (const listener of configListeners) {
    listener();
  }
}

function subscribeConfig(listener: () => void): () => void {
  configListeners.add(listener);
  return () => {
    configListeners.delete(listener);
  };
}

function getSupportedLocalesSnapshot(): string[] {
  return supportedLocales;
}

let bootstrapPromise: Promise<void> | null = null;

function bootstrapLocaleConfig(): Promise<void> {
  bootstrapPromise ??= (async () => {
    try {
      const { data: envelope } = await getConfigApiV1ConfigGet({
        client,
        throwOnError: true,
      });
      const config = envelope.data;
      if (!config) {
        return;
      }
      setSupportedLocales(config.supported_locales);
      // A returning user's saved/profile locale can name a locale the
      // *current* product config no longer supports (it was removed after
      // they picked it) -- fall back to the product's own default rather
      // than keep rendering an orphaned language.
      if (!config.supported_locales.includes(i18next.language)) {
        await i18next.changeLanguage(config.default_locale);
      }
    } catch {
      // Stay on DEFAULT_LOCALE -- see the deny-by-default comment above.
    }
  })();
  return bootstrapPromise;
}

function subscribeLanguageChanged(listener: () => void): () => void {
  i18next.on("languageChanged", listener);
  return () => {
    i18next.off("languageChanged", listener);
  };
}

function getLanguageSnapshot(): string {
  return i18next.language;
}

export function useLocale(): UseLocaleResult {
  const locale = useSyncExternalStore(
    subscribeLanguageChanged,
    getLanguageSnapshot,
  );
  const locales = useSyncExternalStore(
    subscribeConfig,
    getSupportedLocalesSnapshot,
  );

  useEffect(() => {
    void bootstrapLocaleConfig();
  }, []);

  const setLocale = useCallback(async (next: string): Promise<void> => {
    await i18next.changeLanguage(next);
    // A deliberate in-app pick is just as sticky as a signed-in user's saved
    // profile locale -- see lib/i18n.ts's LOCALE_PINNED_KEY comment.
    await pinLocale();
    try {
      await updateMeApiV1AuthMePatch({
        client,
        body: { locale: next },
        throwOnError: true,
      });
    } catch {
      // Not signed in, or the request failed -- the language is already
      // applied locally (and persisted on-device by lib/i18n.ts's
      // languageChanged subscription), it just won't follow this user to
      // another device yet.
    }
  }, []);

  return { locale, supportedLocales: locales, setLocale };
}
