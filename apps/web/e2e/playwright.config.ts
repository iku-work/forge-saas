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
  // T-224: visual.spec.ts's toHaveScreenshot() calls inherit this budget --
  // centralised here rather than passed per-call since every spec in this
  // directory should tolerate the same amount of anti-aliasing noise. 0.01
  // (pixelmatch's own doc example) turned out too loose for this suite:
  // pixelmatch's anti-aliasing detection discards most of the diff along
  // text/border edges, so an 8px translateY() on a whole settings Card --
  // clearly visible to a human -- only registered ~0.4% and slipped
  // through undetected at 0.01. 0.001 was verified (by deliberately
  // shifting a card 8px) to still catch that same regression, while two
  // consecutive unmodified runs in this same container produced an exact
  // 0-pixel diff, so there's no anti-aliasing noise floor to worry about
  // clipping against here.
  expect: {
    toHaveScreenshot: { maxDiffPixelRatio: 0.001 },
  },
  // T-224: one shared directory for every spec's baselines rather than
  // Playwright's default per-spec-file "<name>.spec.ts-snapshots" folder --
  // visual.spec.ts is the only spec with baselines today, but a reviewer
  // scanning for "what pixel baselines exist" shouldn't need to know which
  // spec file each one came from.
  snapshotPathTemplate: "{testDir}/__screenshots__/{arg}-{projectName}-{platform}{ext}",
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
