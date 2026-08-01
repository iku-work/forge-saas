/**
 * T-069: react-i18next bootstrap for the web app.
 *
 * Every translation key is "<namespace>.<path>" (e.g. "auth.login.email_label"),
 * matching @forge/i18n's own t() resolver exactly -- so this file configures
 * i18next with a *single* namespace ("translation") per locale, where that
 * namespace's resources are the merge of every locales/<code>/*.json file
 * (common, auth, tenancy, billing, notifications, emails, errors, validation).
 * Real i18next namespaces (colon-separated) are deliberately not used: they'd
 * require every t() call site to repeat the namespace as a second argument or
 * via "ns:key" syntax, which doesn't match the dot-path convention shared with
 * the backend's app/core/i18n.py and packages/i18n's own t().
 *
 * Locale resources: the *default* locale (English) is statically imported so
 * it's available the instant i18next.init() resolves -- no network/await on
 * the common case. Every other locale is fetched on demand through a custom
 * i18next "backend" plugin (LOCALE_LOADERS below) the first time
 * i18next.changeLanguage() is asked for it, so a product that ships many
 * locales doesn't pay for translations most visitors never select. This is
 * why @forge/i18n's package.json exports a "./locales/*" subpath: it lets
 * this file import a single namespace file directly (one of the
 * @forge/i18n locales/<code>/*.json files), without pulling in
 * @forge/i18n's own bundled dist (which inlines every locale's JSON into one
 * module, eliminating any per-locale code-splitting).
 *
 * The per-locale loaders below list out 8 literal `import()` calls instead of
 * building the specifier from a template string: Rollup only creates a
 * separate chunk for a dynamic import it can resolve at build time, and a
 * cross-package specifier built from a runtime variable (e.g.
 * `import(`@forge/i18n/locales/${locale}/${ns}.json`)`) is not guaranteed to
 * be statically analyzable the same way a same-project relative glob is.
 * Listing each locale explicitly is mechanical but safe -- T-230 (more
 * locales) adds one more entry here.
 */
import i18next, {
  type BackendModule,
  type I18nFormatModule,
  type Resource,
} from "i18next";
import { IntlMessageFormat } from "intl-messageformat";
import { initReactI18next } from "react-i18next";

import {
  DEFAULT_LOCALE,
  isSupportedLocale,
  localeMeta,
  SUPPORTED_LOCALES,
  type SupportedLocale,
  type Translations,
} from "@forge/i18n";

import { resolvePreferredLocale } from "./lib/locale-url";

export const LOCALE_STORAGE_KEY = "forge.locale";

const NAMESPACE = "translation";

// Each locales/<code>/*.json file's content is a plain object of nested
// string leaves (see @forge/i18n/README.md) -- the cast below trusts that
// contract rather than re-deriving TranslationNode's recursive shape from
// each import's own inferred JSON-literal type.
async function loadEn(): Promise<Translations> {
  const [
    common,
    auth,
    tenancy,
    billing,
    files,
    notifications,
    emails,
    errors,
    validation,
  ] = await Promise.all([
    import("@forge/i18n/locales/en/common.json"),
    import("@forge/i18n/locales/en/auth.json"),
    import("@forge/i18n/locales/en/tenancy.json"),
    import("@forge/i18n/locales/en/billing.json"),
    import("@forge/i18n/locales/en/files.json"),
    import("@forge/i18n/locales/en/notifications.json"),
    import("@forge/i18n/locales/en/emails.json"),
    import("@forge/i18n/locales/en/errors.json"),
    import("@forge/i18n/locales/en/validation.json"),
  ]);
  return {
    common: common.default,
    auth: auth.default,
    tenancy: tenancy.default,
    billing: billing.default,
    files: files.default,
    notifications: notifications.default,
    emails: emails.default,
    errors: errors.default,
    validation: validation.default,
  };
}

async function loadFi(): Promise<Translations> {
  const [
    common,
    auth,
    tenancy,
    billing,
    files,
    notifications,
    emails,
    errors,
    validation,
  ] = await Promise.all([
    import("@forge/i18n/locales/fi/common.json"),
    import("@forge/i18n/locales/fi/auth.json"),
    import("@forge/i18n/locales/fi/tenancy.json"),
    import("@forge/i18n/locales/fi/billing.json"),
    import("@forge/i18n/locales/fi/files.json"),
    import("@forge/i18n/locales/fi/notifications.json"),
    import("@forge/i18n/locales/fi/emails.json"),
    import("@forge/i18n/locales/fi/errors.json"),
    import("@forge/i18n/locales/fi/validation.json"),
  ]);
  return {
    common: common.default,
    auth: auth.default,
    tenancy: tenancy.default,
    billing: billing.default,
    files: files.default,
    notifications: notifications.default,
    emails: emails.default,
    errors: errors.default,
    validation: validation.default,
  };
}

async function loadDe(): Promise<Translations> {
  const [
    common,
    auth,
    tenancy,
    billing,
    files,
    notifications,
    emails,
    errors,
    validation,
  ] = await Promise.all([
    import("@forge/i18n/locales/de/common.json"),
    import("@forge/i18n/locales/de/auth.json"),
    import("@forge/i18n/locales/de/tenancy.json"),
    import("@forge/i18n/locales/de/billing.json"),
    import("@forge/i18n/locales/de/files.json"),
    import("@forge/i18n/locales/de/notifications.json"),
    import("@forge/i18n/locales/de/emails.json"),
    import("@forge/i18n/locales/de/errors.json"),
    import("@forge/i18n/locales/de/validation.json"),
  ]);
  return {
    common: common.default,
    auth: auth.default,
    tenancy: tenancy.default,
    billing: billing.default,
    files: files.default,
    notifications: notifications.default,
    emails: emails.default,
    errors: errors.default,
    validation: validation.default,
  };
}

async function loadEs(): Promise<Translations> {
  const [
    common,
    auth,
    tenancy,
    billing,
    files,
    notifications,
    emails,
    errors,
    validation,
  ] = await Promise.all([
    import("@forge/i18n/locales/es/common.json"),
    import("@forge/i18n/locales/es/auth.json"),
    import("@forge/i18n/locales/es/tenancy.json"),
    import("@forge/i18n/locales/es/billing.json"),
    import("@forge/i18n/locales/es/files.json"),
    import("@forge/i18n/locales/es/notifications.json"),
    import("@forge/i18n/locales/es/emails.json"),
    import("@forge/i18n/locales/es/errors.json"),
    import("@forge/i18n/locales/es/validation.json"),
  ]);
  return {
    common: common.default,
    auth: auth.default,
    tenancy: tenancy.default,
    billing: billing.default,
    files: files.default,
    notifications: notifications.default,
    emails: emails.default,
    errors: errors.default,
    validation: validation.default,
  };
}

async function loadFr(): Promise<Translations> {
  const [
    common,
    auth,
    tenancy,
    billing,
    files,
    notifications,
    emails,
    errors,
    validation,
  ] = await Promise.all([
    import("@forge/i18n/locales/fr/common.json"),
    import("@forge/i18n/locales/fr/auth.json"),
    import("@forge/i18n/locales/fr/tenancy.json"),
    import("@forge/i18n/locales/fr/billing.json"),
    import("@forge/i18n/locales/fr/files.json"),
    import("@forge/i18n/locales/fr/notifications.json"),
    import("@forge/i18n/locales/fr/emails.json"),
    import("@forge/i18n/locales/fr/errors.json"),
    import("@forge/i18n/locales/fr/validation.json"),
  ]);
  return {
    common: common.default,
    auth: auth.default,
    tenancy: tenancy.default,
    billing: billing.default,
    files: files.default,
    notifications: notifications.default,
    emails: emails.default,
    errors: errors.default,
    validation: validation.default,
  };
}

async function loadAr(): Promise<Translations> {
  const [
    common,
    auth,
    tenancy,
    billing,
    files,
    notifications,
    emails,
    errors,
    validation,
  ] = await Promise.all([
    import("@forge/i18n/locales/ar/common.json"),
    import("@forge/i18n/locales/ar/auth.json"),
    import("@forge/i18n/locales/ar/tenancy.json"),
    import("@forge/i18n/locales/ar/billing.json"),
    import("@forge/i18n/locales/ar/files.json"),
    import("@forge/i18n/locales/ar/notifications.json"),
    import("@forge/i18n/locales/ar/emails.json"),
    import("@forge/i18n/locales/ar/errors.json"),
    import("@forge/i18n/locales/ar/validation.json"),
  ]);
  return {
    common: common.default,
    auth: auth.default,
    tenancy: tenancy.default,
    billing: billing.default,
    files: files.default,
    notifications: notifications.default,
    emails: emails.default,
    errors: errors.default,
    validation: validation.default,
  };
}

// Add one entry here per new locale (T-230) -- SUPPORTED_LOCALES is the
// candidate list; this map is what actually has to exist for a locale to be
// loadable. A mismatch is a developer error caught immediately by
// LazyBackend.read() throwing "no loader".
const LOCALE_LOADERS: Record<SupportedLocale, () => Promise<Translations>> = {
  en: loadEn,
  fi: loadFi,
  de: loadDe,
  es: loadEs,
  fr: loadFr,
  ar: loadAr,
};

// A hand-rolled i18nFormat plugin rather than i18next-icu: that package's
// published 2.4.3 build does `import IntlMessageFormat from
// "intl-messageformat"`, a default import, but intl-messageformat's own v10
// CJS build only ever exports `IntlMessageFormat` as a *named* export (no
// `.default`) -- so under both Node's and Vite/esbuild's CJS/ESM interop,
// that import resolves to the whole exports object, not the class, and
// `new IntlMessageFormat(...)` throws "is not a constructor". i18next-icu
// swallows that error in its default parseErrorHandler and silently
// returns the unsubstituted "{placeholder}" string -- confirmed by hand
// (see this task's notes) before writing this replacement. @forge/i18n's
// own t() already sidesteps this by using the same named import below.
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

class LazyBackend implements BackendModule {
  static readonly type = "backend";
  type = "backend" as const;

  init(): void {
    // No setup needed -- LOCALE_LOADERS is already a static map.
  }

  read(
    language: string,
    _namespace: string,
    callback: (error: Error | null, data: Resource[string] | false) => void,
  ): void {
    if (!isSupportedLocale(language)) {
      callback(
        new Error(`@forge/i18n: no loader registered for locale "${language}"`),
        false,
      );
      return;
    }
    LOCALE_LOADERS[language]()
      .then((translations) => {
        callback(null, translations);
      })
      .catch((error: unknown) => {
        callback(
          error instanceof Error ? error : new Error(String(error)),
          false,
        );
      });
  }
}

export function readStoredLocale(): string | null {
  try {
    return localStorage.getItem(LOCALE_STORAGE_KEY);
  } catch {
    // Storage disabled (private browsing, etc.) -- fall through to detection.
    return null;
  }
}

/**
 * Which locale a *locale-less* entry point should resolve to (T-233's
 * redirect rule, and this module's own pre-router bootstrap). The signed-in
 * user's own preference is the top tier but isn't knowable here -- the
 * router passes it in from `context.auth.user` once useAuth() has resolved.
 */
export function detectPreferredLocale(
  userLocale?: string | null,
): SupportedLocale {
  return resolvePreferredLocale({
    userLocale,
    storedLocale: readStoredLocale(),
    browserLocales: navigator.languages,
  });
}

// True only while the URL -- not the user -- is driving a language change.
// T-233: "the active URL locale wins over the stored preference for
// rendering [...] but does not silently overwrite" it. Rendering a shared
// /de/... link must therefore not leave "de" behind in localStorage (nor,
// via useLocale().setLocale, in User.locale) for the visitor's next
// locale-less visit.
let localeChangeIsUrlDriven = false;

/**
 * Applies the locale a matched `/$locale/...` route carries. Idempotent, so
 * every navigation can call it unconditionally.
 */
export async function setUrlLocale(locale: SupportedLocale): Promise<void> {
  if (i18next.language === locale) {
    return;
  }
  localeChangeIsUrlDriven = true;
  try {
    await i18next.changeLanguage(locale);
  } finally {
    localeChangeIsUrlDriven = false;
  }
}

let initPromise: Promise<typeof i18next> | null = null;

export function initI18n(): Promise<typeof i18next> {
  initPromise ??= (async () => {
    const initialLocale = detectPreferredLocale();
    const resources: Resource = {
      [DEFAULT_LOCALE]: { [NAMESPACE]: await loadEn() },
    };

    await i18next
      .use(new LazyBackend())
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
        partialBundledLanguages: true,
        interpolation: { escapeValue: false },
        react: { useSuspense: false },
      });

    document.documentElement.lang = i18next.language;
    document.documentElement.dir = localeMeta(i18next.language)?.dir ?? "ltr";
    i18next.on("languageChanged", (lng: string) => {
      document.documentElement.lang = lng;
      document.documentElement.dir = localeMeta(lng)?.dir ?? "ltr";
      if (localeChangeIsUrlDriven) {
        // Viewing a shared /de/... link is not a preference change -- see
        // localeChangeIsUrlDriven above. <html lang>/<html dir> still track
        // it, since those describe what's on screen right now.
        return;
      }
      try {
        localStorage.setItem(LOCALE_STORAGE_KEY, lng);
      } catch {
        // Storage disabled -- the in-memory language is still correct for
        // this session, it just won't survive a reload.
      }
    });

    return i18next;
  })();
  return initPromise;
}

export default i18next;
