// A read-only pass over a *deployed* environment, to catch the class of
// problem a local run cannot: config drift, a missing env var, a stale
// bundle, an edge rule that only exists in Caddyfile.prod.
//
// Every real defect this repo shipped between 2026-09-12 and 2026-09-14 was
// visible from the outside within seconds of looking -- a login form shown
// to a signed-in user, a page wider than the screen, an API call answering
// 500. None needed a database or a Stripe key to see. This is that look,
// automated.
//
// Skipped unless E2E_PROD_URL, E2E_PROD_EMAIL and E2E_PROD_PASSWORD are all
// set, so it never runs by accident against something real. Use a throwaway
// account: it signs in, which creates a session row and an audit event.
//
// Deliberately makes no writes. No checkout, no upload, no settings change,
// no webhook. If a check here needs to mutate state, it belongs in
// journey.spec.ts against the local stack instead.
import { expect, test, type Response } from "@playwright/test";

const PROD_URL = process.env["E2E_PROD_URL"];
const PROD_EMAIL = process.env["E2E_PROD_EMAIL"];
const PROD_PASSWORD = process.env["E2E_PROD_PASSWORD"];

const configured = Boolean(PROD_URL && PROD_EMAIL && PROD_PASSWORD);

// The pages a signed-in customer can reach on the free tier. Reports is
// absent on purpose: it is gated on the plan's advanced_reports flag.
const PAGES = [
  ["dashboard", "/en"],
  ["files", "/en/files"],
  ["profile", "/en/settings/profile"],
  ["security", "/en/settings/security"],
  ["organization", "/en/settings/org"],
  ["members", "/en/settings/members"],
  ["billing", "/en/settings/billing"],
  ["notifications", "/en/settings/notifications"],
] as const;

// 320 is the narrowest width still worth supporting, 390 a current iPhone.
// Both, because the overflow bugs this repo shipped appeared at one width
// and not the other.
const WIDTHS = [320, 390, 1280];

test.describe("deployed environment smoke", () => {
  test.skip(
    !configured,
    "set E2E_PROD_URL, E2E_PROD_EMAIL and E2E_PROD_PASSWORD to run this",
  );
  test.use({ contextOptions: { reducedMotion: "reduce" } });

  test("every page loads, fits, and makes no failing request", async ({
    browser,
  }) => {
    const context = await browser.newContext({ baseURL: PROD_URL });
    const page = await context.newPage();

    const failures: string[] = [];
    page.on("response", (response: Response) => {
      if (!response.url().includes("/api/v1/")) return;
      if (response.status() < 400) return;
      // The cold-boot 401 before a session exists, and the per-user cap of
      // five concurrent notification streams, which a run of full page
      // loads transiently exceeds. Both explained at length in
      // modules.spec.ts.
      if (response.status() === 401 && response.url().includes("/auth/refresh"))
        return;
      if (
        response.status() === 429 &&
        response.url().includes("/notifications/stream")
      ) {
        return;
      }
      failures.push(
        `${String(response.status())} ${response.url().split("/api/v1/")[1]}`,
      );
    });

    await page.goto("/en/login");
    await page.getByTestId("login-email-input").fill(PROD_EMAIL as string);
    await page
      .getByTestId("login-password-input")
      .fill(PROD_PASSWORD as string);
    await page.getByTestId("login-submit").click();
    await expect(page.getByTestId("app-shell")).toBeVisible();

    // The second-tab bug: a signed-in visitor shown a login form, who then
    // rotates their own refresh cookie by obeying it. Shipped to production
    // and found by hand.
    await test.step("a signed-in visitor is not shown a login form", async () => {
      const second = await context.newPage();
      await second.goto("/en/login");
      await expect(second.getByTestId("app-shell")).toBeVisible();
      expect(new URL(second.url()).pathname).toBe("/en");
      await second.close();
    });

    for (const width of WIDTHS) {
      await page.setViewportSize({ width, height: width < 700 ? 844 : 900 });
      for (const [name, path] of PAGES) {
        await test.step(`${name} @ ${String(width)}px`, async () => {
          await page.goto(path);
          await expect(page.getByTestId("app-shell")).toBeVisible();
          // A heading proves the route rendered its page rather than an
          // empty shell or an error boundary.
          await expect(page.getByTestId("page-title")).toBeVisible();
          const overflow = await page.evaluate(
            () =>
              document.documentElement.scrollWidth -
              document.documentElement.clientWidth,
          );
          expect(
            overflow,
            `${path} scrolls sideways at ${String(width)}px`,
          ).toBeLessThanOrEqual(0);
        });
      }
    }

    expect(failures, "a deployed page made a failing API call").toEqual([]);
    await context.close();
  });
});
