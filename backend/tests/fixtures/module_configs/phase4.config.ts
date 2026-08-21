/**
 * Product configuration — owned by the product, not the template.
 * Keep in sync with backend/product/config.py.
 * The template never modifies this file.
 */
import type { ProductConfig } from "@forge/types";

export const config = {
  name: "ForgeSaaS",
  primaryColor: "#7c6ff7",
  logoUrl: "/logo.svg",
  supportEmail: "support@example.com",
  modules: {
    // T-291 combination C's twin of tests/fixtures/module_configs/phase4.py:
    // the template defaults with the two off-by-default Phase 4 modules on.
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
    publicApi: true,
    enterpriseSso: true,
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
