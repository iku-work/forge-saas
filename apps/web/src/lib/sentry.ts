import * as Sentry from "@sentry/react";

// T-201: gated on VITE_SENTRY_DSN so local dev (which never sets it) never
// talks to Sentry at all -- mirrors the backend's SENTRY_DSN-unset no-op.
export function initSentry(): void {
  const dsn = import.meta.env.VITE_SENTRY_DSN as string | undefined;
  if (!dsn) {
    return;
  }
  Sentry.init({
    dsn,
    environment: import.meta.env.MODE,
    release: import.meta.env.VITE_SENTRY_RELEASE as string | undefined,
    // No personal data, the same rule as the backend's send_default_pii=False.
    // `dataCollection` replaces the deprecated `sendDefaultPii`, and setting it
    // switches every omitted field to Sentry's collect-everything default, so
    // each field that can carry personal data is turned off here explicitly.
    dataCollection: {
      userInfo: false,
      cookies: false,
      httpHeaders: false,
      httpBodies: [],
      urlQueryParams: false,
      genAI: { inputs: false, outputs: false },
      databaseQueryData: false,
    },
  });
}

// id only -- never email or name (same rule as the backend and mobile).
export function setSentryUser(user: { id: string } | null): void {
  Sentry.setUser(user);
}
