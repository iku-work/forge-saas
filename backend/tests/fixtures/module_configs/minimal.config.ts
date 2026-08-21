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
    // T-291 combination B's twin of tests/fixtures/module_configs/minimal.py:
    // every optional module off. `marketing: false` is the one the frontend
    // half of that job actually reads -- apps/marketing's build guard (T-300)
    // is the only thing in the workspace that consults this file.
    billing: false,
    notifications: false,
    fileStorage: false,
    featureFlags: false,
    gdpr: false,
    admin: false,
    oauth: false,
    totp2fa: false,
    multiOrg: false,
    usageMetering: false,
    outboundWebhooks: false,
    marketing: false,
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
