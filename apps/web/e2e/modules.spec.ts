// Is every enabled module actually reachable by a customer?
//
// `product/config.py` says which modules are on, and /api/v1/config reports
// the same list at runtime -- but both answer "is it configured", not "can
// somebody use it". A module can be enabled, its routes mounted and its
// tables migrated, while the surface a customer would touch is missing or
// broken. That gap is what this spec measures, and it found one: the gdpr
// module is enabled and its endpoints work, yet nothing in the web app ever
// calls requestExportApiV1MeExportGet or the account-deletion route, so a
// customer has no way to exercise either. Recorded in KNOWN_UNREACHABLE
// below rather than quietly skipped.
//
// Every page also asserts that none of its own API calls failed. That is the
// cheapest real signal of "connected": a module whose page renders while its
// requests 4xx is not working, and a heading assertion alone would not
// notice.
import { expect, test, type Page, type Response } from "@playwright/test";

const EMAIL = "admin@example.com";
const PASSWORD = "DevPassword123!";

/**
 * Enabled in product/config.py, but with no way in from the web app.
 *
 * Listed, not skipped: the point of this file is to be able to say which
 * modules a customer can actually reach, and an omission would read as
 * coverage.
 *
 * A record, not an enforced check -- nothing here fails when a surface gets
 * built, so delete the entry and add a step when it does. The one thing the
 * bottom test does enforce is the other direction: a module switched on with
 * no entry here and no step fails it.
 */
const KNOWN_UNREACHABLE: Record<string, string> = {
  gdpr:
    "endpoints exist (GET /me/export, DELETE /me) but nothing in apps/web " +
    "calls them; only consent recording is wired, via lib/useConsentSync.ts",
};

/** Requests a page made that came back an error, ignoring the expected ones. */
function collectApiFailures(page: Page): string[] {
  const failures: string[] = [];
  page.on("response", (response: Response) => {
    if (!response.url().includes("/api/v1/")) return;
    if (response.status() < 400) return;
    // A signed-out bootstrap 401 on /auth/refresh is the normal first call
    // on a cold page and says nothing about a module.
    if (response.status() === 401 && response.url().includes("/auth/refresh")) return;
    // The notification stream caps *concurrency*, not request rate: five
    // open streams per user, each released when it closes and pruned after
    // 90s (app/modules/notifications/sse.py). Every `page.goto` here is a
    // full document load that opens a new one before the previous has
    // finished closing, so a walk of eight pages transiently exceeds five.
    // That is this test's own doing, not a module fault -- a real SPA
    // session opens one per tab. Excluded deliberately rather than silently:
    // a *sustained* 429 here, or one on the first page load, would be real.
    if (response.status() === 429 && response.url().includes("/notifications/stream")) {
      return;
    }
    // The admin surface refusing a superadmin without 2FA is the gate
    // working; the step below asserts that refusal on purpose.
    if (response.status() === 403 && response.url().includes("/admin/")) return;
    failures.push(`${String(response.status())} ${response.url().split("/api/v1/")[1]}`);
  });
  return failures;
}

test.describe("every enabled module is reachable", () => {
  test.use({ reducedMotion: "reduce" });

  test("a customer can reach each module's surface", async ({ page }) => {
    const failures = collectApiFailures(page);

    await page.goto("/en/login");
    await page.getByTestId("login-email-input").fill(EMAIL);
    await page.getByTestId("login-password-input").fill(PASSWORD);
    await page.getByTestId("login-submit").click();
    await expect(page.getByTestId("app-shell")).toBeVisible();

    await test.step("file_storage: the Files page", async () => {
      await page.getByTestId("nav-files").click();
      await expect(page.getByRole("heading", { name: /files/i })).toBeVisible();
    });

    await test.step("notifications: preferences and the bell", async () => {
      await page.goto("/en/settings/notifications");
      await expect(
        page.getByRole("heading", { name: /notification/i }),
      ).toBeVisible();
      // The bell is the module's only always-on surface, and it lives in the
      // shell rather than on a page -- worth opening, since a broken feed
      // renders a bell that does nothing.
      await page.getByTestId("notification-bell-trigger").click();
      await expect(page.getByTestId("notification-panel")).toBeVisible();
      await page.keyboard.press("Escape");
    });

    await test.step("multi_org: the org switcher lists and offers to create", async () => {
      await page.goto("/en");
      await page.getByTestId("org-switcher-trigger").click();
      await expect(page.getByTestId("org-switcher-create")).toBeVisible();
      await page.keyboard.press("Escape");
    });

    await test.step("totp_2fa and oauth: both on the Security page", async () => {
      await page.goto("/en/settings/security");
      await expect(page.getByRole("heading", { name: /security/i })).toBeVisible();
      // totp_2fa off would remove the control entirely, not just disable it.
      await expect(page.getByRole("button", { name: /2fa/i })).toBeVisible();
      // oauth surfaces as the linkable providers.
      await expect(page.getByText(/google/i).first()).toBeVisible();
    });

    await test.step("billing: the plans surface", async () => {
      await page.goto("/en/settings/billing");
      await expect(page.getByTestId("billing-current-plan")).toBeVisible();
      await expect(page.getByTestId("billing-upgrade-pro")).toBeVisible();
    });

    await test.step("admin: refused without 2FA, which is the design", async () => {
      // The seeded superadmin has no TOTP, so the admin surface must refuse
      // rather than render. Asserting the refusal is what proves the gate
      // exists; asserting the page would prove the opposite.
      await page.goto("/en/admin/users");
      await expect(page.getByText(/two-factor/i)).toBeVisible();
    });

    expect(failures, "no module's page should make a failing API call").toEqual([]);
  });

  test("the runtime module list matches what this spec covers", async ({
    request,
  }) => {
    // Guards against a module being switched on in product/config.py with no
    // surface added here, which would leave this file quietly partial.
    const config = (await (await request.get("/api/v1/config")).json()) as {
      data: { modules: Record<string, boolean> };
    };
    const enabled = Object.entries(config.data.modules)
      .filter(([, on]) => on)
      .map(([name]) => name);

    const covered = new Set([
      "billing",
      "notifications",
      "file_storage",
      "multi_org",
      "totp_2fa",
      "oauth",
      "admin",
      // Surfaces only as the Reports nav entry, which is additionally gated
      // on the plan's advanced_reports flag -- covered by the billing
      // journey rather than here, where the org is on the free tier.
      "feature_flags",
      // Its own Caddy host, not part of the app shell.
      "marketing",
      ...Object.keys(KNOWN_UNREACHABLE),
    ]);

    const uncovered = enabled.filter((name) => !covered.has(name));
    expect(
      uncovered,
      "a module was enabled without a surface being covered here",
    ).toEqual([]);
  });
});
