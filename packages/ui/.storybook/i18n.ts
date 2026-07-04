/**
 * T-150: minimal react-i18next bootstrap for Storybook.
 *
 * apps/web/src/i18n.ts (T-069) lazy-loads locales on demand and persists the
 * user's choice -- neither of which Storybook needs. This mirrors just the
 * parts every component under test actually depends on: the same single
 * "translation" namespace, dot-separated keys, and ICU interpolation (via
 * the same hand-rolled i18nFormat plugin apps/web/src/i18n.ts uses -- see
 * that file for why i18next-icu itself can't be used), built from
 * @forge/i18n's English JSON so story text matches production copy instead
 * of raw keys.
 */
import i18next, { type I18nFormatModule } from "i18next";
import { IntlMessageFormat } from "intl-messageformat";
import { initReactI18next } from "react-i18next";

import authEn from "@forge/i18n/locales/en/auth.json";
import billingEn from "@forge/i18n/locales/en/billing.json";
import commonEn from "@forge/i18n/locales/en/common.json";
import emailsEn from "@forge/i18n/locales/en/emails.json";
import errorsEn from "@forge/i18n/locales/en/errors.json";
import filesEn from "@forge/i18n/locales/en/files.json";
import notificationsEn from "@forge/i18n/locales/en/notifications.json";
import tenancyEn from "@forge/i18n/locales/en/tenancy.json";
import validationEn from "@forge/i18n/locales/en/validation.json";

const NAMESPACE = "translation";

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

void i18next
  .use(new IcuFormat())
  .use(initReactI18next)
  .init({
    lng: "en",
    fallbackLng: "en",
    ns: [NAMESPACE],
    defaultNS: NAMESPACE,
    nsSeparator: false,
    keySeparator: ".",
    interpolation: { escapeValue: false },
    resources: {
      en: {
        [NAMESPACE]: {
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
      },
    },
  });

export default i18next;
