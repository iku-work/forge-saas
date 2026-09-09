import { IntlMessageFormat } from "intl-messageformat";

export {
  isSupportedLocale,
  type LocaleMeta,
  LOCALES as LOCALE_REGISTRY,
  localeMeta,
  SUPPORTED_LOCALES,
  type SupportedLocale,
} from "./locales";
import {
  isSupportedLocale,
  SUPPORTED_LOCALES,
  type SupportedLocale,
} from "./locales";

import authEn from "../locales/en/auth.json";
import billingEn from "../locales/en/billing.json";
import commonEn from "../locales/en/common.json";
import emailsEn from "../locales/en/emails.json";
import errorsEn from "../locales/en/errors.json";
import filesEn from "../locales/en/files.json";
import marketingEn from "../locales/en/marketing.json";
import notificationsEn from "../locales/en/notifications.json";
import tenancyEn from "../locales/en/tenancy.json";
import validationEn from "../locales/en/validation.json";

import authFi from "../locales/fi/auth.json";
import billingFi from "../locales/fi/billing.json";
import commonFi from "../locales/fi/common.json";
import emailsFi from "../locales/fi/emails.json";
import errorsFi from "../locales/fi/errors.json";
import filesFi from "../locales/fi/files.json";
import marketingFi from "../locales/fi/marketing.json";
import notificationsFi from "../locales/fi/notifications.json";
import tenancyFi from "../locales/fi/tenancy.json";
import validationFi from "../locales/fi/validation.json";

import authDe from "../locales/de/auth.json";
import billingDe from "../locales/de/billing.json";
import commonDe from "../locales/de/common.json";
import emailsDe from "../locales/de/emails.json";
import errorsDe from "../locales/de/errors.json";
import filesDe from "../locales/de/files.json";
import marketingDe from "../locales/de/marketing.json";
import notificationsDe from "../locales/de/notifications.json";
import tenancyDe from "../locales/de/tenancy.json";
import validationDe from "../locales/de/validation.json";

import authEs from "../locales/es/auth.json";
import billingEs from "../locales/es/billing.json";
import commonEs from "../locales/es/common.json";
import emailsEs from "../locales/es/emails.json";
import errorsEs from "../locales/es/errors.json";
import filesEs from "../locales/es/files.json";
import marketingEs from "../locales/es/marketing.json";
import notificationsEs from "../locales/es/notifications.json";
import tenancyEs from "../locales/es/tenancy.json";
import validationEs from "../locales/es/validation.json";

import authFr from "../locales/fr/auth.json";
import billingFr from "../locales/fr/billing.json";
import commonFr from "../locales/fr/common.json";
import emailsFr from "../locales/fr/emails.json";
import errorsFr from "../locales/fr/errors.json";
import filesFr from "../locales/fr/files.json";
import marketingFr from "../locales/fr/marketing.json";
import notificationsFr from "../locales/fr/notifications.json";
import tenancyFr from "../locales/fr/tenancy.json";
import validationFr from "../locales/fr/validation.json";

import authAr from "../locales/ar/auth.json";
import billingAr from "../locales/ar/billing.json";
import commonAr from "../locales/ar/common.json";
import emailsAr from "../locales/ar/emails.json";
import errorsAr from "../locales/ar/errors.json";
import filesAr from "../locales/ar/files.json";
import marketingAr from "../locales/ar/marketing.json";
import notificationsAr from "../locales/ar/notifications.json";
import tenancyAr from "../locales/ar/tenancy.json";
import validationAr from "../locales/ar/validation.json";

// A leaf is always a string -- locale JSON files never contain numbers,
// booleans, or arrays. See packages/i18n/README.md.
export type TranslationNode = string | { [key: string]: TranslationNode };
export type Translations = Record<string, Record<string, TranslationNode>>;

export const DEFAULT_LOCALE = "en";

// SUPPORTED_LOCALES / SupportedLocale live in ./locales.ts (the T-230
// metadata registry) and are re-exported above -- what this package ships
// data for. A product's own subset (which of these it actually exposes to
// users) is configured separately in product.config.py / product.config.ts
// -- see README.md.

const TRANSLATIONS_BY_LOCALE: Record<SupportedLocale, Translations> = {
  en: {
    common: commonEn,
    auth: authEn,
    tenancy: tenancyEn,
    billing: billingEn,
    files: filesEn,
    marketing: marketingEn,
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
    marketing: marketingFi,
    notifications: notificationsFi,
    emails: emailsFi,
    errors: errorsFi,
    validation: validationFi,
  },
  de: {
    common: commonDe,
    auth: authDe,
    tenancy: tenancyDe,
    billing: billingDe,
    files: filesDe,
    marketing: marketingDe,
    notifications: notificationsDe,
    emails: emailsDe,
    errors: errorsDe,
    validation: validationDe,
  },
  es: {
    common: commonEs,
    auth: authEs,
    tenancy: tenancyEs,
    billing: billingEs,
    files: filesEs,
    marketing: marketingEs,
    notifications: notificationsEs,
    emails: emailsEs,
    errors: errorsEs,
    validation: validationEs,
  },
  fr: {
    common: commonFr,
    auth: authFr,
    tenancy: tenancyFr,
    billing: billingFr,
    files: filesFr,
    marketing: marketingFr,
    notifications: notificationsFr,
    emails: emailsFr,
    errors: errorsFr,
    validation: validationFr,
  },
  ar: {
    common: commonAr,
    auth: authAr,
    tenancy: tenancyAr,
    billing: billingAr,
    files: filesAr,
    marketing: marketingAr,
    notifications: notificationsAr,
    emails: emailsAr,
    errors: errorsAr,
    validation: validationAr,
  },
};

export function loadLocale(locale: string): Translations {
  if (!isSupportedLocale(locale)) {
    throw new Error(
      `@forge/i18n: "${locale}" is not a supported locale (${SUPPORTED_LOCALES.join(", ")})`,
    );
  }
  return TRANSLATIONS_BY_LOCALE[locale];
}

function resolveKey(
  translations: Translations,
  key: string,
): string | undefined {
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
    resolveKey(loadLocale(effectiveLocale), key) ??
    resolveKey(loadLocale(DEFAULT_LOCALE), key);

  if (message === undefined) {
    throw new Error(
      `@forge/i18n: missing key "${key}" in both "${effectiveLocale}" and "${DEFAULT_LOCALE}"`,
    );
  }

  if (!message.includes("{")) {
    return message;
  }
  return new IntlMessageFormat(message, effectiveLocale).format(
    params,
  ) as string;
}
