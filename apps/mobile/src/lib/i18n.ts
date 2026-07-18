/**
 * T-070: react-i18next bootstrap for the mobile app. Mirrors
 * apps/web/src/i18n.ts's contract exactly -- same dot-path translation keys
 * ("<namespace>.<path>", matching @forge/i18n's own t() resolver), same
 * hand-rolled ICU i18nFormat plugin (see that file's docstring for why
 * i18next-icu itself is broken with this repo's intl-messageformat version) --
 * with two differences forced by the platform:
 *
 *  1. There is no code-splitting boundary on React Native: the whole JS
 *     bundle ships inside the app binary (or an OTA update) regardless of
 *     how many locales it contains, so unlike web's per-locale lazy
 *     `import()` backend plugin, every locale is loaded eagerly via
 *     @forge/i18n's own loadLocale() (its bundled dist already inlines every
 *     locale's JSON into one module -- exactly what's wanted here, whereas
 *     web avoids that same dist specifically to keep per-locale chunks).
 *  2. First-launch detection and persistence use expo-localization and
 *     AsyncStorage instead of navigator.languages/localStorage.
 *
 * T-231 (RTL): syncNativeDirection() below wires I18nManager to the active
 * locale's `dir` and reloads the app when it flips, so the auth screens and
 * `app/(app)/settings.tsx` (which this task verified) render mirrored under
 * `ar`. It's a native, process-wide flag, not a live style -- see that
 * function's own comment for why a flip needs a reload. Deliberately not
 * done here: a full NativeWind class sweep of every other screen the way
 * packages/ui's web components got (T-231's spec allows deferring the rest
 * of mobile RTL polish with this note; web/packages/ui may not defer).
 */
import { useEffect } from "react";
import AsyncStorage from "@react-native-async-storage/async-storage";
import * as Localization from "expo-localization";
import * as Updates from "expo-updates";
import i18next, { type I18nFormatModule, type Resource } from "i18next";
import { IntlMessageFormat } from "intl-messageformat";
import { initReactI18next } from "react-i18next";
import { I18nManager } from "react-native";

import {
  DEFAULT_LOCALE,
  loadLocale,
  localeMeta,
  SUPPORTED_LOCALES,
  type SupportedLocale,
} from "@forge/i18n";

export const LOCALE_STORAGE_KEY = "forge.locale";
// Separate from LOCALE_STORAGE_KEY (which records the last-known language
// unconditionally, for fast cold-start restoration regardless of *why* it
// changed) -- this records whether that language reflects a deliberate
// choice (the settings language picker, or a signed-in user's saved profile
// locale) as opposed to just the live device setting. Without this
// distinction, syncSystemLocale() below would block itself: applying a
// device-locale change calls i18next.changeLanguage(), which the
// languageChanged listener persists into LOCALE_STORAGE_KEY just like any
// other change, so a naive "skip if something is already stored" guard
// would only ever fire once and then never again.
const LOCALE_PINNED_KEY = "forge.locale.pinned";

const NAMESPACE = "translation";

// Mirrors apps/web/src/i18n.ts's IcuFormat exactly (duplicated rather than
// shared -- see that file's comment on why i18next-icu@2.4.3 can't be used
// with this repo's intl-messageformat@10, and apps/mobile/src/lib/config.ts's
// own top comment on why mobile reimplements rather than importing
// web-specific bootstrap code).
class IcuFormat implements I18nFormatModule {
  type = "i18nFormat" as const;
  private readonly cache = new Map<string, IntlMessageFormat>();

  init(): void {
    // No setup needed -- formatters are built lazily per (lng, message).
  }

  parse(res: unknown, options: Record<string, unknown>, lng: string): unknown {
    if (typeof res !== "string" || !res.includes("{")) {
      return res;
    }
    const cacheKey = `${lng} ${res}`;
    let formatter = this.cache.get(cacheKey);
    if (!formatter) {
      formatter = new IntlMessageFormat(res, lng);
      this.cache.set(cacheKey, formatter);
    }
    try {
      return formatter.format(options);
    } catch {
      return res;
    }
  }
}

async function readStoredLocale(): Promise<string | null> {
  try {
    return await AsyncStorage.getItem(LOCALE_STORAGE_KEY);
  } catch {
    // Storage unavailable -- fall through to detection.
    return null;
  }
}

async function isLocalePinned(): Promise<boolean> {
  try {
    return (await AsyncStorage.getItem(LOCALE_PINNED_KEY)) === "1";
  } catch {
    return false;
  }
}

// Exported for lib/locale.ts's setLocale -- a deliberate in-app language
// pick is just as sticky as a signed-in user's saved profile locale.
export async function pinLocale(): Promise<void> {
  try {
    await AsyncStorage.setItem(LOCALE_PINNED_KEY, "1");
  } catch {
    // Best effort -- the language is still applied for this session.
  }
}

function isSupported(
  locale: string | null | undefined,
): locale is SupportedLocale {
  return (
    locale != null && (SUPPORTED_LOCALES as readonly string[]).includes(locale)
  );
}

// First-launch flow (T-070): only the device's top-preference language is
// checked against SUPPORTED_LOCALES, falling straight back to DEFAULT_LOCALE
// otherwise -- deliberately not a multi-candidate loop over every device
// locale the way apps/web/src/i18n.ts walks navigator.languages, since that
// isn't part of this task's spec.
function detectDeviceLocale(): SupportedLocale {
  const top = Localization.getLocales()[0]?.languageCode ?? null;
  return isSupported(top) ? top : DEFAULT_LOCALE;
}

async function detectInitialLocale(): Promise<SupportedLocale> {
  const stored = await readStoredLocale();
  if (isSupported(stored)) {
    return stored;
  }
  return detectDeviceLocale();
}

// T-231: React Native's layout-direction engine is a native, process-wide
// flag (I18nManager.isRTL) -- unlike the DOM's `dir` attribute, it can't be
// live-toggled. forceRTL() only takes effect after the JS bundle reloads, so
// switching into/out of ar mid-session requires an actual reload for the
// mirrored layout to appear; until that reload happens the app keeps
// rendering in whatever direction it launched with, even though i18next's
// own language (strings, ICU plurals, etc.) already switched. allowRTL(true)
// is safe to call every time -- it only controls whether the OS is allowed
// to report RTL to begin with, not the current setting.
function syncNativeDirection(lng: string): void {
  I18nManager.allowRTL(true);
  const isRtl = localeMeta(lng)?.dir === "rtl";
  if (I18nManager.isRTL === isRtl) {
    return;
  }
  I18nManager.forceRTL(isRtl);
  // Reload so the native layout engine picks up the new direction. Throws
  // in environments expo-updates doesn't support reloading in (Expo Go,
  // some dev-client configurations) -- caught rather than surfaced, since
  // the alternative is a broken app, not a missing feature: the language
  // itself is already applied, and a manual app restart (documented in
  // TEMPLATE.md / this comment) finishes the RTL flip in those cases.
  Updates.reloadAsync().catch(() => {
    // No-op reload path (dev/Expo Go) -- see comment above.
  });
}

let initPromise: Promise<typeof i18next> | null = null;

export function initI18n(): Promise<typeof i18next> {
  initPromise ??= (async () => {
    const initialLocale = await detectInitialLocale();
    const resources: Resource = Object.fromEntries(
      SUPPORTED_LOCALES.map((code) => [
        code,
        { [NAMESPACE]: loadLocale(code) },
      ]),
    );

    await i18next
      .use(new IcuFormat())
      .use(initReactI18next)
      .init({
        resources,
        lng: initialLocale,
        fallbackLng: DEFAULT_LOCALE,
        supportedLngs: SUPPORTED_LOCALES,
        ns: [NAMESPACE],
        defaultNS: NAMESPACE,
        nsSeparator: false,
        keySeparator: ".",
        interpolation: { escapeValue: false },
        react: { useSuspense: false },
      });

    syncNativeDirection(initialLocale);

    // Every later language change -- an explicit pick in settings
    // (lib/locale.ts's setLocale), a signed-in user's saved profile locale
    // (applyProfileLocale below), or a live OS language change
    // (syncSystemLocale below) -- becomes the new "last known good" locale
    // for the next cold start. i18next.init()'s own `lng` option sets the
    // language directly without going through changeLanguage(), so this
    // listener never fires for the locale detectInitialLocale() just chose.
    i18next.on("languageChanged", (lng: string) => {
      syncNativeDirection(lng);
      AsyncStorage.setItem(LOCALE_STORAGE_KEY, lng).catch(() => {
        // Storage disabled -- the in-memory language is still correct for
        // this session, it just won't survive a restart.
      });
    });

    return i18next;
  })();
  return initPromise;
}

// Called once apps/mobile/app/_layout.tsx's session bootstrap resolves: a
// signed-in user's saved preference outranks the device-language guess
// initI18n() made before anyone was known to be logged in, but never
// overrides a choice already pinned (a prior call to this same function, or
// the settings language picker -- see LOCALE_PINNED_KEY above). Applying it
// here pins it too, so a later live device-locale change (syncSystemLocale)
// doesn't immediately override the account preference that was just loaded.
export async function applyProfileLocale(
  profileLocale: string | null | undefined,
): Promise<void> {
  if (!profileLocale || (await isLocalePinned())) {
    return;
  }
  if (isSupported(profileLocale) && profileLocale !== i18next.language) {
    await i18next.changeLanguage(profileLocale);
    await pinLocale();
  }
}

// Fed by useSystemLocaleSync() below whenever expo-localization reports the
// OS language changed, so the app keeps following a live device-settings
// change instead of only the language detected at cold start. Deliberately
// never pins -- this is the opposite of a deliberate choice, so a
// *subsequent* OS change (or a later sign-in) must still be free to move
// the language again.
export async function syncSystemLocale(
  languageCode: string | null | undefined,
): Promise<void> {
  if (await isLocalePinned()) {
    return;
  }
  if (isSupported(languageCode) && languageCode !== i18next.language) {
    await i18next.changeLanguage(languageCode);
  }
}

// expo-localization's useLocales() hook re-renders with a fresh locale list
// whenever the OS language changes while the app is running -- there is no
// plain event-emitter API for this in expo-localization@16. Mounted once
// from the root layout.
export function useSystemLocaleSync(): void {
  const locales = Localization.useLocales();
  useEffect(() => {
    void syncSystemLocale(locales[0]?.languageCode ?? null);
  }, [locales]);
}

export default i18next;
