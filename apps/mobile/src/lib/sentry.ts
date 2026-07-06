/**
 * Sentry wiring (T-201) -- mirrors apps/web/src/lib/sentry.ts's shape
 * (init gated on an env-provided DSN, `setSentryUser` taking id only) with
 * one native-only addition: `release` includes the EAS Update id, not just
 * the app version, so a stack trace from a device that already picked up an
 * OTA update still maps to the JS bundle that actually ran rather than the
 * one baked into the last native build.
 */
import * as Sentry from "@sentry/react-native";
import Constants from "expo-constants";
import * as Updates from "expo-updates";

function getRelease(): string {
  const appVersion = Constants.expoConfig?.version ?? "0.0.0";
  return `${appVersion}+${Updates.updateId ?? "embedded"}`;
}

export function initSentry(): void {
  const dsn = process.env.EXPO_PUBLIC_SENTRY_DSN;
  if (!dsn) {
    return;
  }
  Sentry.init({
    dsn,
    environment: process.env.NODE_ENV === "production" ? "production" : "development",
    release: getRelease(),
    sendDefaultPii: false,
  });
}

// id only -- never email or name (same rule as the backend and web).
export function setSentryUser(user: { id: string } | null): void {
  Sentry.setUser(user);
}
