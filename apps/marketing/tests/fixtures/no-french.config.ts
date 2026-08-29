/**
 * A product config with `fr` switched off (T-305).
 *
 * "Disabling a locale removes its pages and its alternates from the whole
 * build" is one of T-305's tests, and there is no other configuration in the
 * repo that turns a locale off: product.config.ts enables all six, and the
 * module-matrix fixtures vary modules rather than languages. `fr` is the
 * choice because it carries no content of its own -- no blog post, no guide,
 * no legal document is written in French -- so what disappears with it is
 * exactly the machinery under test and nothing else.
 *
 * Everything else matches the committed product.config.ts. It is reached by
 * FORGE_PRODUCT_CONFIG, which moves the whole app -- guard, locale list and
 * pages alike -- onto this file.
 */
import type { ProductConfig } from "@forge/types";

export const config = {
  name: "ForgeSaaS",
  primaryColor: "#7c6ff7",
  logoUrl: "/logo.svg",
  supportEmail: "support@example.com",
  modules: {
    billing: true,
    notifications: true,
    fileStorage: true,
    featureFlags: true,
    gdpr: true,
    admin: true,
    oauth: true,
    totp2fa: true,
    multiOrg: true,
    usageMetering: false,
    outboundWebhooks: false,
    marketing: true,
    publicApi: false,
    enterpriseSso: false,
  },
  locales: {
    en: true,
    fi: true,
    de: true,
    es: true,
    fr: false,
    ar: true,
    default: "en",
  },
} satisfies ProductConfig;
