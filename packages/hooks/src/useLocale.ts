import { useCallback, useEffect, useSyncExternalStore } from "react";
import i18next from "i18next";

import {
  getConfigApiV1ConfigGet,
  updateMeApiV1AuthMePatch,
} from "@forge/api-client";
import { DEFAULT_LOCALE } from "@forge/i18n";

export interface UseLocaleResult {
  locale: string;
  supportedLocales: string[];
  setLocale: (locale: string) => Promise<void>;
}

// Deny-by-default mirrors useModules.ts: until /config resolves, only
// DEFAULT_LOCALE is offered rather than @forge/i18n's full package-level
// SUPPORTED_LOCALES, since a product may ship a narrower subset.
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

// Platform-agnostic by design (T-070): switching the active language and
// persisting it to User.locale both go through i18next/the API client
// directly, neither of which differs between web and mobile. What *does*
// differ -- detecting the initial language, lazy-loading a non-default
// locale's resources, and remembering an explicit choice across reloads --
// lives entirely in each platform's own i18n bootstrap (apps/web/src/i18n.ts;
// mobile's counterpart follows the same contract in T-070), via i18next's
// own "languageChanged" event, which this hook also just subscribes to.
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
    try {
      await updateMeApiV1AuthMePatch({
        body: { locale: next },
        throwOnError: true,
      });
    } catch {
      // Not signed in, or the request failed -- the language is already
      // applied locally (and persisted on-device by the platform's i18n
      // bootstrap), it just won't follow this user to another device yet.
    }
  }, []);

  return { locale, supportedLocales: locales, setLocale };
}
