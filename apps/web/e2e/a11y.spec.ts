// T-221: axe-core WCAG A/AA scan appended to the E2E suite. Runs against
// every key page in its realistic (populated, not empty-state) form --
// login, register, an authenticated dashboard, a members list with more
// than one row, the billing page, a settings page, the notification
// center opened, and the file upload page. checkA11y() (helpers/axe.ts)
// fails the test on any serious/critical violation not covered by
// a11y-allowlist.ts; moderate-and-below are recorded as annotations
// instead of failing the run.
//
// One thing this task's own spec calls for that this file deliberately
// doesn't do yet:
//   - Both color schemes: the product has no dark mode today (Storybook's
//     `.dark` class toggle is a Storybook-only preview affordance, not a
//     user-facing toggle anywhere in apps/web) -- nothing to scan twice.
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

test("key pages have no serious/critical WCAG A/AA violations", async ({ page, browser }, testInfo) => {
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
    await panel.evaluate((el) => Promise.all(el.getAnimations().map((a) => a.finished)));
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

    const teammateContext = await browser.newContext({ ignoreHTTPSErrors: true });
    const teammatePage = await teammateContext.newPage();
    try {
      await teammatePage.goto("/register");
      await teammatePage.getByTestId("register-email-input").fill(teammateEmail);
      await teammatePage.getByTestId("register-password-input").fill(password);
      await teammatePage.getByTestId("register-submit").click();
      await expect(teammatePage.getByTestId("register-success")).toBeVisible();

      const teammateVerifyLink = await waitForLink(
        teammateEmail,
        /https:\/\/localhost\/[a-z]{2}\/verify-email\?token=[\w-]+/,
      );
      await teammatePage.goto(teammateVerifyLink);
      await expect(teammatePage.getByTestId("verify-email-success")).toBeVisible();
      await teammatePage.getByTestId("verify-email-continue-to-login").click();

      await teammatePage.getByTestId("login-email-input").fill(teammateEmail);
      await teammatePage.getByTestId("login-password-input").fill(password);
      await teammatePage.getByTestId("login-submit").click();
      await expect(teammatePage.getByTestId("create-first-org-prompt")).toBeVisible();

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

  await page.getByTestId("create-org-name-input").fill(`A11y RTL Co ${rtlRunId}`);
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
