import { defineConfig, devices } from "@playwright/test";

import { BASE_URL } from "./config";

export default defineConfig({
  testDir: ".",
  // The journey itself hits several auth endpoints guarded by a 5/minute
  // per-IP limit (backend/app/middleware/rate_limit.py's auth_limit) --
  // running the chromium and webkit projects at the same time means both
  // share that one quota from the runner's single IP and can 429 each
  // other's registrations. One project at a time avoids that collision;
  // there's only a single spec here, so this costs nothing but wall time.
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["html", { open: "never" }], ["list"]] : "list",
  use: {
    baseURL: BASE_URL,
    // The dev Caddyfile serves a `tls internal` (self-signed) leaf cert --
    // see that file's own comment for why the ZAP scanner and this suite
    // both need to trust it rather than a real CA-issued one.
    ignoreHTTPSErrors: true,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "webkit", use: { ...devices["Desktop Safari"] } },
  ],
});
