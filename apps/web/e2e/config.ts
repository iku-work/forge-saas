// Shared between playwright.config.ts and the helpers -- both need the same
// base URL and neither can import from the other without a cycle.
export const BASE_URL = process.env.E2E_BASE_URL ?? "https://localhost";
export const MAILHOG_URL = process.env.E2E_MAILHOG_URL ?? "http://localhost:8025";
