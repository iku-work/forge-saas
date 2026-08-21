/**
 * A product config with `marketing` on and `billing` off (T-300).
 *
 * The landing page drops the subscription highlight when there is no billing
 * module, and nothing else in this repo can exercise that: the module-matrix
 * combination that turns billing off (`minimal`) turns marketing off with it,
 * so the site is never built there. This fixture is the one configuration
 * where the page's module awareness is observable.
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
    billing: false,
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
    fr: true,
    ar: true,
    default: "en",
  },
} satisfies ProductConfig;
