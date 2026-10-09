// T-224: pixel-diff regression for the app shell's two most-seen surfaces --
// dashboard and settings (profile + org tabs) -- at desktop and mobile
// viewport widths. Logs in once as the seeded superadmin
// (backend/app/core/seed.py's SUPERADMIN_EMAIL/PASSWORD, idempotent across
// runs, unlike acceptance.spec.ts's per-run signup) so every snapshot starts
// from the same deterministic account + org rather than re-registering per
// page, which also keeps this file to a single login against the 5/minute
// auth rate limit that playwright.config.ts's own comment documents.
//
// Update workflow: after an *intentional* layout change, regenerate
// baselines with `pnpm e2e visual.spec.ts --update-snapshots`, then review
// the new PNGs as an actual diff in the PR -- a baseline change nobody looks
// at defeats the point of this suite. Baselines are platform-specific
// (Playwright appends `-{project}-{platform}` to each filename
// automatically): regenerate them from the same Linux environment CI uses
// (e.g. the `mcr.microsoft.com/playwright` docker image), not a local
// macOS/Windows checkout, or every CI run will report "no baseline found".
import { expect, test } from "@playwright/test";

import { stabilizeForSnapshot } from "./helpers/visual";

const SUPERADMIN_EMAIL = "admin@example.com";
const SUPERADMIN_PASSWORD = "DevPassword123!";

const DESKTOP_VIEWPORT = { width: 1280, height: 800 };
const MOBILE_VIEWPORT = { width: 375, height: 812 };

// Every dynamic region across the three pages below, gathered in one place
// since none of them are page-specific: an avatar's initials are
// deterministic given the fixed seed email/org name, but masking rather than
// relying on that keeps the suite correct the day either page grows a real
// (non-deterministic) avatar image. notification-badge won't exist at all
// for a freshly seeded account with zero notifications -- toHaveScreenshot's
// mask silently no-ops on a locator that matches nothing, so listing it
// unconditionally is cheap insurance rather than a live requirement.
function dynamicRegionMasks(page: import("@playwright/test").Page) {
  return [
    page.getByTestId("user-menu-avatar"),
    page.getByTestId("notification-badge"),
    page.getByTestId("profile-avatar"),
    page.getByTestId("org-avatar"),
  ];
}

// journey.spec.ts gives the same account a newer "Journey <runId>" org, and
// the app opens on the newest one. Pick the seeded org so the shot is stable.
async function useSeededOrg(page: import("@playwright/test").Page) {
  await page.getByTestId("org-switcher-trigger").click();
  await page
    .getByTestId("org-switcher-option")
    .filter({ hasText: "Forge Demo" })
    .click();
  await expect(page.getByTestId("org-switcher-trigger")).toContainText(
    "Forge Demo",
  );
  // The pointer is left where the menu item was, over a nav link: hover state.
  await page.mouse.move(0, 0);
}

const SNAPSHOT_TARGETS = [
  // The checklist, not the shell: the shell renders before the checklist's
  // queries return, and a shot taken then shows its loading skeleton. The
  // seeded account always has open steps, so the checklist is always there.
  { path: "/", name: "dashboard", readyTestId: "getting-started" },
  {
    path: "/settings/profile",
    name: "settings-profile",
    readyTestId: "profile-avatar",
  },
  { path: "/settings/org", name: "settings-org", readyTestId: "org-avatar" },
];

// T-233: every URL is locale-prefixed now. Navigating to the prefixed form
// directly (rather than letting the locale-less path redirect) keeps each
// snapshot one navigation, and is what makes the ar run below actually
// render in Arabic -- the locale in the URL is what decides, not a stored
// preference.
function localePath(locale: string, path: string): string {
  return path === "/" ? `/${locale}` : `/${locale}${path}`;
}

const VIEWPORTS = [
  { name: "desktop", size: DESKTOP_VIEWPORT },
  { name: "mobile", size: MOBILE_VIEWPORT },
];

test.describe("visual regression: dashboard + settings", () => {
  test.use({ reducedMotion: "reduce" });

  test("app shell, dashboard, and settings pages at desktop + mobile widths", async ({
    page,
  }) => {
    await page.setViewportSize(DESKTOP_VIEWPORT);
    await page.goto("/en/login");
    await page.getByTestId("login-email-input").fill(SUPERADMIN_EMAIL);
    await page.getByTestId("login-password-input").fill(SUPERADMIN_PASSWORD);
    await page.getByTestId("login-submit").click();
    await expect(page.getByTestId("app-shell")).toBeVisible();
    await useSeededOrg(page);

    for (const viewport of VIEWPORTS) {
      await page.setViewportSize(viewport.size);
      for (const target of SNAPSHOT_TARGETS) {
        await test.step(`${target.name} @ ${viewport.name}`, async () => {
          await page.goto(localePath("en", target.path));
          await expect(page.getByTestId(target.readyTestId)).toBeVisible();
          await stabilizeForSnapshot(page);
          await expect(page).toHaveScreenshot(
            `${target.name}-${viewport.name}.png`,
            {
              mask: dynamicRegionMasks(page),
            },
          );
        });
      }
    }
  });

  // T-231: dashboard + settings-profile in the ar (rtl) locale, desktop
  // width only -- proving the layout mirrors (sidebar, nav, and text
  // alignment flip), not re-running the full page x viewport matrix above
  // in a second language.
  //
  // T-233 replaced the old addInitScript that seeded localStorage's
  // "forge.locale": the URL is the source of truth for the rendered locale
  // now, so /ar/login is both simpler and a stricter test -- the seeded
  // superadmin's own User.locale is "en", and rtl still has to win here
  // because that's what the URL says.
  test("dashboard and settings-profile mirror correctly in the ar (rtl) locale", async ({
    page,
  }) => {
    await page.setViewportSize(DESKTOP_VIEWPORT);
    await page.goto("/ar/login");
    // Polled, not read once. `goto` resolves on load, and `dir` is set by the
    // app as it boots -- chromium happens to get there first and webkit does
    // not, so a single read is a race that fails in one browser and passes in
    // the other for reasons that have nothing to do with RTL.
    await expect
      .poll(() => page.evaluate(() => document.documentElement.dir))
      .toBe("rtl");
    await page.getByTestId("login-email-input").fill(SUPERADMIN_EMAIL);
    await page.getByTestId("login-password-input").fill(SUPERADMIN_PASSWORD);
    await page.getByTestId("login-submit").click();
    await expect(page.getByTestId("app-shell")).toBeVisible();
    await useSeededOrg(page);

    for (const target of SNAPSHOT_TARGETS.filter(
      (t) => t.name !== "settings-org",
    )) {
      await test.step(`${target.name} @ desktop (ar)`, async () => {
        await page.goto(localePath("ar", target.path));
        await expect(page.getByTestId(target.readyTestId)).toBeVisible();
        await stabilizeForSnapshot(page);
        await expect(page).toHaveScreenshot(`${target.name}-desktop-ar.png`, {
          mask: dynamicRegionMasks(page),
        });
      });
    }
  });
});
