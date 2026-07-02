import { IntlMessageFormat } from "intl-messageformat";

import authEn from "../locales/en/auth.json";
import billingEn from "../locales/en/billing.json";
import commonEn from "../locales/en/common.json";
import emailsEn from "../locales/en/emails.json";
import errorsEn from "../locales/en/errors.json";
import filesEn from "../locales/en/files.json";
import notificationsEn from "../locales/en/notifications.json";
import tenancyEn from "../locales/en/tenancy.json";
import validationEn from "../locales/en/validation.json";

import authFi from "../locales/fi/auth.json";
import billingFi from "../locales/fi/billing.json";
import commonFi from "../locales/fi/common.json";
import emailsFi from "../locales/fi/emails.json";
import errorsFi from "../locales/fi/errors.json";
import filesFi from "../locales/fi/files.json";
import notificationsFi from "../locales/fi/notifications.json";
import tenancyFi from "../locales/fi/tenancy.json";
import validationFi from "../locales/fi/validation.json";

// A leaf is always a string -- locale JSON files never contain numbers,
// booleans, or arrays. See packages/i18n/README.md.
export type TranslationNode = string | { [key: string]: TranslationNode };
export type Translations = Record<string, Record<string, TranslationNode>>;

export const DEFAULT_LOCALE = "en";

// What this package ships data for. A product's own subset (which of these
// it actually exposes to users) is configured separately in
// product.config.py / product.config.ts -- see README.md.
export const SUPPORTED_LOCALES = ["en", "fi"] as const;
export type SupportedLocale = (typeof SUPPORTED_LOCALES)[number];

const LOCALES: Record<SupportedLocale, Translations> = {
  en: {
    common: commonEn,
    auth: authEn,
    tenancy: tenancyEn,
    billing: billingEn,
    files: filesEn,
    notifications: notificationsEn,
    emails: emailsEn,
    errors: errorsEn,
    validation: validationEn,
  },
  fi: {
    common: commonFi,
    auth: authFi,
    tenancy: tenancyFi,
    billing: billingFi,
    files: filesFi,
    notifications: notificationsFi,
    emails: emailsFi,
    errors: errorsFi,
    validation: validationFi,
  },
};

function isSupportedLocale(locale: string): locale is SupportedLocale {
  return (SUPPORTED_LOCALES as readonly string[]).includes(locale);
}

export function loadLocale(locale: string): Translations {
  if (!isSupportedLocale(locale)) {
    throw new Error(
      `@forge/i18n: "${locale}" is not a supported locale (${SUPPORTED_LOCALES.join(", ")})`,
    );
  }
  return LOCALES[locale];
}

function resolveKey(translations: Translations, key: string): string | undefined {
  const [namespace, ...path] = key.split(".");
  if (!namespace) return undefined;

  let node: TranslationNode | undefined = translations[namespace];
  for (const segment of path) {
    if (typeof node !== "object") return undefined;
    node = node[segment];
  }
  return typeof node === "string" ? node : undefined;
}

/**
 * Looks up `key` (e.g. "auth.login.email_label") in `locale`, falling back to
 * DEFAULT_LOCALE when the key is missing from the target locale -- a partial
 * locale (like "fi" here) must never surface a raw key string to a user.
 * Values containing `{params}` are run through ICU MessageFormat.
 */
export function t(
  key: string,
  params: Record<string, string | number> = {},
  locale: string = DEFAULT_LOCALE,
): string {
  const effectiveLocale = isSupportedLocale(locale) ? locale : DEFAULT_LOCALE;
  const message =
    resolveKey(loadLocale(effectiveLocale), key) ?? resolveKey(loadLocale(DEFAULT_LOCALE), key);

  if (message === undefined) {
    throw new Error(
      `@forge/i18n: missing key "${key}" in both "${effectiveLocale}" and "${DEFAULT_LOCALE}"`,
    );
  }

  if (!message.includes("{")) {
    return message;
  }
  return new IntlMessageFormat(message, effectiveLocale).format(params) as string;
}
