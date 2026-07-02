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
  },
  locales: {
    supported: ["en", "fi"],
    default: "en",
  },
} satisfies ProductConfig;
