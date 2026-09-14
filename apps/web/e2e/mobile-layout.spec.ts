// Horizontal-overflow gate for every in-app route at phone widths.
//
// Three mobile layout bugs shipped in a row before this existed, and the
// suite could not have caught any of them: the Playwright projects are
// Desktop Chrome and Desktop Safari, visual.spec.ts's "mobile" viewport is
// 375px but only covers three pages, and a jsdom test cannot measure
// layout at all. What every one of those bugs had in common is that the
// document became wider than the screen -- which is one number, needs no
// baseline, and is cheap to assert everywhere.
//
// Deliberately not a screenshot test: a pixel baseline for 15 routes would
// be 15 more PNGs to regenerate on every intentional change, and it would
// answer "did this page change?" rather than "does this page fit?".
//
// The bugs this would have caught:
//   - the shell column had no `min-w-0`, so a flex item's default
//     `min-width: auto` let the members list push the whole document to
//     408px inside a 360px viewport;
//   - AdminLayout's header held a wordmark, four nav links and an account
//     block in one unwrappable row -- 901px in a 360px viewport, on every
//     admin page.
import { expect, test } from "@playwright/test";

const SUPERADMIN_EMAIL = "admin@example.com";
const SUPERADMIN_PASSWORD = "DevPassword123!";

// 320 is the narrowest width still worth supporting (iPhone SE 1st gen, and
// the floor Tailwind's own docs assume); 390 is a current iPhone. Anything
// that fits both fits everything between.
const WIDTHS = [320, 390];

// `readyTestId` is what proves the route actually rendered rather than
// bouncing to /login -- measuring an unmounted page would pass trivially.
const ROUTES: { path: string; name: string; readyTestId: string }[] = [
  { path: "/en", name: "dashboard", readyTestId: "app-shell" },
  { path: "/en/files", name: "files", readyTestId: "app-shell" },
  { path: "/en/settings/profile", name: "profile", readyTestId: "app-shell" },
  { path: "/en/settings/security", name: "security", readyTestId: "app-shell" },
  { path: "/en/settings/org", name: "org", readyTestId: "app-shell" },
  { path: "/en/settings/members", name: "members", readyTestId: "app-shell" },
  { path: "/en/settings/billing", name: "billing", readyTestId: "app-shell" },
  {
    path: "/en/settings/notifications",
    name: "notifications",
    readyTestId: "app-shell",
  },
  { path: "/en/settings/api-keys", name: "api-keys", readyTestId: "app-shell" },
  { path: "/en/settings/webhooks", name: "webhooks", readyTestId: "app-shell" },
];

// AdminLayout is a different shell with its own header, so it needs its own
// coverage -- and it was the worse of the two offenders.
const ADMIN_ROUTES: { path: string; name: string }[] = [
  { path: "/en/admin", name: "admin-dashboard" },
  { path: "/en/admin/users", name: "admin-users" },
  { path: "/en/admin/orgs", name: "admin-orgs" },
  { path: "/en/admin/audit", name: "admin-audit" },
];

/** How far the document scrolls past the viewport, in CSS pixels. */
async function horizontalOverflow(
  page: import("@playwright/test").Page,
): Promise<number> {
  return page.evaluate(() => {
    const de = document.documentElement;
    return de.scrollWidth - de.clientWidth;
  });
}

/** The shallowest elements sticking out, for a failure message worth reading. */
async function offenders(
  page: import("@playwright/test").Page,
): Promise<string[]> {
  return page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    const found: { depth: number; line: string }[] = [];
    for (const el of Array.from(document.querySelectorAll("body *"))) {
      const b = el.getBoundingClientRect();
      if (b.width === 0 && b.height === 0) continue;
      if (b.right <= vw + 1 && b.width <= vw + 1) continue;
      let depth = 0;
      for (let p: Element | null = el; p; p = p.parentElement) depth++;
      found.push({
        depth,
        line: `<${el.tagName.toLowerCase()}> w=${Math.round(b.width)} right=${Math.round(
          b.right,
        )} class="${(el.getAttribute("class") ?? "").slice(0, 120)}"`,
      });
    }
    // Shallowest first: a deep offender is usually just a child of the real
    // one being stretched.
    found.sort((a, b) => a.depth - b.depth);
    return found.slice(0, 5).map((f) => f.line);
  });
}

test.describe("no horizontal overflow at phone widths", () => {
  test.use({ reducedMotion: "reduce" });

  test("every in-app route fits the screen", async ({ page }) => {
    await page.setViewportSize({ width: WIDTHS[0], height: 844 });
    await page.goto("/en/login");
    await page.getByTestId("login-email-input").fill(SUPERADMIN_EMAIL);
    await page.getByTestId("login-password-input").fill(SUPERADMIN_PASSWORD);
    await page.getByTestId("login-submit").click();
    await expect(page.getByTestId("app-shell")).toBeVisible();

    for (const width of WIDTHS) {
      await page.setViewportSize({ width, height: 844 });
      for (const route of ROUTES) {
        await test.step(`${route.name} @ ${width}px`, async () => {
          await page.goto(route.path);
          await expect(page.getByTestId(route.readyTestId)).toBeVisible();
          const overflow = await horizontalOverflow(page);
          expect(
            overflow,
            `${route.path} overflows by ${overflow}px at ${width}px:\n  ${(
              await offenders(page)
            ).join("\n  ")}`,
          ).toBeLessThanOrEqual(0);
        });
      }

      for (const route of ADMIN_ROUTES) {
        await test.step(`${route.name} @ ${width}px`, async () => {
          await page.goto(route.path);
          // AdminLayout has no app-shell testid; its <main> is the marker.
          await expect(page.locator("main").first()).toBeVisible();
          const overflow = await horizontalOverflow(page);
          expect(
            overflow,
            `${route.path} overflows by ${overflow}px at ${width}px:\n  ${(
              await offenders(page)
            ).join("\n  ")}`,
          ).toBeLessThanOrEqual(0);
        });
      }
    }
  });

  // The drawer is the shell's only mobile-only surface, and it shipped
  // anchored to nothing: `inset-block-0`/`inset-inline-start-0` are CSS
  // property names, not Tailwind utilities, so they emitted no rule and the
  // panel rendered below the fold behind a full-screen overlay. Asserting it
  // is on screen is what a class-name assertion could not do.
  test("the navigation drawer opens into the viewport", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/en/login");
    await page.getByTestId("login-email-input").fill(SUPERADMIN_EMAIL);
    await page.getByTestId("login-password-input").fill(SUPERADMIN_PASSWORD);
    await page.getByTestId("login-submit").click();
    await expect(page.getByTestId("app-shell")).toBeVisible();

    await page.getByTestId("nav-drawer-trigger").click();

    const drawer = page.getByTestId("sheet-content");
    await expect(drawer).toBeVisible();
    const box = await drawer.boundingBox();
    expect(box).not.toBeNull();
    // Anchored to the top-left of the viewport, not floating past its edges.
    expect(box!.y).toBeLessThanOrEqual(1);
    expect(box!.x).toBeLessThanOrEqual(1);
    expect(box!.width).toBeGreaterThan(200);
    expect(box!.height).toBeGreaterThan(400);
    // And the nav inside it is actually reachable.
    await expect(drawer.getByTestId("nav-dashboard")).toBeVisible();
  });
});
