// T-221: axe-core WCAG A/AA scan appended to the E2E suite. Runs against
// every key page in its realistic (populated, not empty-state) form --
// login, register, an authenticated dashboard, a members list with more
// than one row, the billing page, a settings page, the notification
// center opened, and the file upload page. checkA11y() (helpers/axe.ts)
// fails the test on any serious/critical violation not covered by
// a11y-allowlist.ts; moderate-and-below are recorded as annotations
// instead of failing the run.
//
// Both colour schemes are now scanned. That note used to read "the product
// has no dark mode today"; F-004 gave it one (Settings -> Profile ->
// Appearance, persisted to User.theme), so the dark theme gets its own pass
// at the bottom of this file -- contrast is the axe rule most likely to
// regress in a second palette, and the whole point of the dark token set is
// that it was derived to clear AA rather than dimmed by eye.
//
// The `ar` (RTL) locale is covered below (T-231) -- dashboard + a settings
// page, not the full page list above, mirroring visual.spec.ts's own ar
// scope: this is a mirrored-layout regression check, not a second full
// sweep of every page in a second language.
//
// Setup here deliberately duplicates a small slice of acceptance.spec.ts's
// (T-220) own register/verify/invite flow rather than importing from it --
// this suite has a different, narrower goal (get every key page into a
// populated state, not exercise the business journey itself), and keeping
// it self-contained means a change to one spec can't silently break the
// other. Same data-testid-only rule applies (enforced by e2e-web's own grep
// step across the whole apps/web/e2e directory).
import { expect, test } from "@playwright/test";

import { checkA11y } from "./helpers/axe";
import { waitForLink } from "./helpers/mailhog";

const runId = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
const ownerEmail = `a11y-owner+${runId}@example.com`;
const teammateEmail = `a11y-teammate+${runId}@example.com`;
const password = `A11y-Journey-${runId}!Aa1`;

test("key pages have no serious/critical WCAG A/AA violations", async ({
  page,
  browser,
}, testInfo) => {
  // One test, many pages, an axe run on each: ~19s on chromium, past the 30s
  // default on webkit's slower axe.
  test.slow(testInfo.project.name === "webkit", "axe is slower on webkit");
  await test.step("login", async () => {
    await page.goto("/login");
    await expect(page.getByTestId("login-email-input")).toBeVisible();
    await checkA11y(page, testInfo, "login");
  });

  await test.step("register", async () => {
    await page.goto("/register");
    await expect(page.getByTestId("register-email-input")).toBeVisible();
    await checkA11y(page, testInfo, "register");
  });

  // --- Register -> verify -> log in -> create an org, to reach the
  // authenticated app shell every remaining page lives behind. ---
  await page.getByTestId("register-email-input").fill(ownerEmail);
  await page.getByTestId("register-password-input").fill(password);
  await page.getByTestId("register-submit").click();
  await expect(page.getByTestId("register-success")).toBeVisible();

  const verifyLink = await waitForLink(
    ownerEmail,
    /https:\/\/localhost\/[a-z]{2}\/verify-email\?token=[\w-]+/,
  );
  await page.goto(verifyLink);
  await expect(page.getByTestId("verify-email-success")).toBeVisible();
  await page.getByTestId("verify-email-continue-to-login").click();

  await page.getByTestId("login-email-input").fill(ownerEmail);
  await page.getByTestId("login-password-input").fill(password);
  await page.getByTestId("login-submit").click();
  await expect(page.getByTestId("create-first-org-prompt")).toBeVisible();

  await page.getByTestId("create-org-name-input").fill(`A11y Co ${runId}`);
  await page.getByTestId("create-org-submit").click();
  await expect(page.getByTestId("app-shell")).toBeVisible();

  await test.step("dashboard", async () => {
    await checkA11y(page, testInfo, "dashboard");
  });

  await test.step("notification center (open)", async () => {
    await page.getByTestId("notification-bell-trigger").click();
    const panel = page.getByTestId("notification-panel");
    await expect(panel).toBeVisible();
    // Radix's own open/fade-in animation (data-[state=open]:animate-in
    // fade-in-0 in PopoverContent) briefly renders the panel at less than
    // full opacity -- scanning mid-fade produces a false-positive
    // color-contrast violation on its text. Waiting for the animation to
    // finish scans the panel in the same fully-opaque state a real user
    // would perceive it in.
    await panel.evaluate((el) =>
      Promise.all(el.getAnimations().map((a) => a.finished)),
    );
    await checkA11y(page, testInfo, "notification-center-open");
    await page.keyboard.press("Escape");
  });

  await test.step("members list (with data)", async () => {
    await page.goto("/settings/members");
    await page.getByTestId("members-invite-button").click();
    await page.getByTestId("invite-email-input").fill(teammateEmail);
    await page.getByTestId("invite-role-select").selectOption("member");
    await page.getByTestId("invite-submit").click();
    await expect(page.getByTestId("invite-email-input")).toBeHidden();

    // The invite is now pending, so the card below the member list has a row
    // -- and this is the populated form of it that axe should see (T-221's
    // "realistic, not empty-state" rule). It also proves the send closes the
    // loop: the row only appears here if the dialog's success path busts the
    // invitations query.
    await expect(page.getByTestId("invitation-row")).toHaveCount(1);
    await checkA11y(page, testInfo, "members-list-pending-invitation");

    const inviteLink = await waitForLink(
      teammateEmail,
      /https:\/\/localhost\/[a-z]{2}\/accept-invitation\/[\w.-]+/,
    );

    const teammateContext = await browser.newContext({
      ignoreHTTPSErrors: true,
    });
    const teammatePage = await teammateContext.newPage();
    try {
      await teammatePage.goto("/register");
      await teammatePage
        .getByTestId("register-email-input")
        .fill(teammateEmail);
      await teammatePage.getByTestId("register-password-input").fill(password);
      await teammatePage.getByTestId("register-submit").click();
      await expect(teammatePage.getByTestId("register-success")).toBeVisible();

      const teammateVerifyLink = await waitForLink(
        teammateEmail,
        /https:\/\/localhost\/[a-z]{2}\/verify-email\?token=[\w-]+/,
      );
      await teammatePage.goto(teammateVerifyLink);
      await expect(
        teammatePage.getByTestId("verify-email-success"),
      ).toBeVisible();
      await teammatePage.getByTestId("verify-email-continue-to-login").click();

      await teammatePage.getByTestId("login-email-input").fill(teammateEmail);
      await teammatePage.getByTestId("login-password-input").fill(password);
      await teammatePage.getByTestId("login-submit").click();
      await expect(
        teammatePage.getByTestId("create-first-org-prompt"),
      ).toBeVisible();

      await teammatePage.goto(inviteLink);
      await teammatePage.getByTestId("accept-invitation-button").click();
      await expect(teammatePage.getByTestId("app-shell")).toBeVisible();
    } finally {
      await teammateContext.close();
    }

    await page.reload();
    await expect(page.getByTestId("member-row")).toHaveCount(2);
    // ...and the invitation they accepted has left the pending list, which
    // is the backend's accepted_at filter proving itself end to end.
    await expect(page.getByTestId("no-pending-invitations")).toBeVisible();
    await checkA11y(page, testInfo, "members-list");
  });

  await test.step("billing page", async () => {
    await page.goto("/settings/billing");
    await expect(page.getByTestId("billing-current-plan")).toBeVisible();
    await checkA11y(page, testInfo, "billing");
  });

  await test.step("settings", async () => {
    await page.goto("/settings/profile");
    await expect(page.getByTestId("app-shell")).toBeVisible();
    await checkA11y(page, testInfo, "settings-profile");
  });

  await test.step("file upload", async () => {
    await page.goto("/files");
    await expect(page.getByTestId("app-shell")).toBeVisible();
    await checkA11y(page, testInfo, "file-upload");
  });
});

// T-231: the ar (rtl) locale, on the two pages visual.spec.ts also snapshots
// -- a mirrored-layout regression check, not a second full sweep of the
// page list above. It doesn't exercise the language switcher UI itself
// (already covered by apps/web/src/layouts/AppLayoutRtlSwitch.test.tsx).
//
// T-233: reaches ar through /ar/... URLs rather than the localStorage seed
// this used to use. That seed stopped meaning anything past the login step
// once the URL became the source of truth -- the account registered below
// has the default "en" locale, so every post-login navigation resolved back
// to /en/... and the two a11y checks silently became ltr checks. Each
// check is now preceded by its own `dir` assertion so that can't recur
// unnoticed.
test("ar (rtl) locale has no serious/critical WCAG A/AA violations", async ({
  page,
}, testInfo) => {
  const rtlRunId = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
  const email = `a11y-rtl+${rtlRunId}@example.com`;
  const rtlPassword = `A11y-Rtl-${rtlRunId}!Aa1`;

  await page.goto("/ar/register");
  await expect(page.getByTestId("register-email-input")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.dir)).toBe("rtl");
  await page.getByTestId("register-email-input").fill(email);
  await page.getByTestId("register-password-input").fill(rtlPassword);
  await page.getByTestId("register-submit").click();
  await expect(page.getByTestId("register-success")).toBeVisible();

  const verifyLink = await waitForLink(
    email,
    /https:\/\/localhost\/[a-z]{2}\/verify-email\?token=[\w-]+/,
  );
  await page.goto(verifyLink);
  await expect(page.getByTestId("verify-email-success")).toBeVisible();
  await page.getByTestId("verify-email-continue-to-login").click();

  await page.getByTestId("login-email-input").fill(email);
  await page.getByTestId("login-password-input").fill(rtlPassword);
  await page.getByTestId("login-submit").click();
  await expect(page.getByTestId("create-first-org-prompt")).toBeVisible();

  await page
    .getByTestId("create-org-name-input")
    .fill(`A11y RTL Co ${rtlRunId}`);
  await page.getByTestId("create-org-submit").click();
  await expect(page.getByTestId("app-shell")).toBeVisible();

  await test.step("dashboard (ar)", async () => {
    await page.goto("/ar");
    await expect(page.getByTestId("app-shell")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.dir)).toBe("rtl");
    await checkA11y(page, testInfo, "dashboard-ar");
  });

  await test.step("settings (ar)", async () => {
    await page.goto("/ar/settings/profile");
    await expect(page.getByTestId("app-shell")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.dir)).toBe("rtl");
    await checkA11y(page, testInfo, "settings-profile-ar");
  });
});

// F-004: the same key pages again, in the dark theme.
//
// The theme is applied by seeding localStorage before the app boots rather
// than by driving the settings UI: the boot script in index.html reads that
// key synchronously on first paint, so this reproduces exactly what a
// returning user with a saved preference gets -- and it does not depend on
// the profile page still laying its control out the same way.
test("dark theme has no serious/critical WCAG A/AA violations", async ({
  page,
}, testInfo) => {
  const darkRunId = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
  const email = `a11y-dark+${darkRunId}@example.com`;
  const darkPassword = `A11y-Dark-${darkRunId}!Aa1`;

  await page.addInitScript(() => {
    window.localStorage.setItem("forge.theme", "dark");
  });

  await page.goto("/register");
  await page.getByTestId("register-email-input").fill(email);
  await page.getByTestId("register-password-input").fill(darkPassword);
  await page.getByTestId("register-submit").click();
  await expect(page.getByTestId("register-success")).toBeVisible();

  const verifyLink = await waitForLink(
    email,
    /https:\/\/localhost\/[a-z]{2}\/verify-email\?token=[\w-]+/,
  );
  await page.goto(verifyLink);
  await expect(page.getByTestId("verify-email-success")).toBeVisible();
  await page.getByTestId("verify-email-continue-to-login").click();

  await test.step("login (dark)", async () => {
    await expect(page.getByTestId("login-email-input")).toBeVisible();
    expect(
      await page.evaluate(() =>
        document.documentElement.classList.contains("dark"),
      ),
    ).toBe(true);
    await checkA11y(page, testInfo, "login-dark");
  });

  await page.getByTestId("login-email-input").fill(email);
  await page.getByTestId("login-password-input").fill(darkPassword);
  await page.getByTestId("login-submit").click();
  await expect(page.getByTestId("create-first-org-prompt")).toBeVisible();

  await page
    .getByTestId("create-org-name-input")
    .fill(`A11y Dark Co ${darkRunId}`);
  await page.getByTestId("create-org-submit").click();
  await expect(page.getByTestId("app-shell")).toBeVisible();

  await test.step("dashboard (dark)", async () => {
    await checkA11y(page, testInfo, "dashboard-dark");
  });

  await test.step("command palette (dark)", async () => {
    await page.getByTestId("palette-trigger").click();
    await expect(page.getByTestId("palette-nav-/")).toBeVisible();
    // Same false positive as the notification panel above: the dialog fades
    // and zooms in, and a scan mid-animation reads its text at partial
    // opacity. The animation is on the dialog, not the item, so wait for
    // every finite animation or transition on the page (an infinite one, a
    // skeleton's pulse, would never settle). A cancelled one rejects
    // `finished` (WebKit cancels a transition that another replaces); it has
    // stopped either way.
    await page.evaluate(() =>
      Promise.all(
        document
          .getAnimations()
          .filter((a) => a.effect?.getTiming().iterations !== Infinity)
          .map((a) => a.finished.catch(() => undefined)),
      ),
    );
    await checkA11y(page, testInfo, "command-palette-dark");
    await page.keyboard.press("Escape");
  });

  await test.step("settings (dark)", async () => {
    await page.goto("/en/settings/profile");
    await expect(page.getByTestId("theme-toggle")).toBeVisible();
    await checkA11y(page, testInfo, "settings-profile-dark");
  });
});
