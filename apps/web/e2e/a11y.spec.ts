// T-221: axe-core WCAG A/AA scan appended to the E2E suite. Runs against
// every key page in its realistic (populated, not empty-state) form --
// login, register, an authenticated dashboard, a members list with more
// than one row, the billing page, a settings page, the notification
// center opened, and the file upload page. checkA11y() (helpers/axe.ts)
// fails the test on any serious/critical violation not covered by
// a11y-allowlist.ts; moderate-and-below are recorded as annotations
// instead of failing the run.
//
// Two things this task's own spec calls for that this file deliberately
// doesn't do yet:
//   - Both color schemes: the product has no dark mode today (Storybook's
//     `.dark` class toggle is a Storybook-only preview affordance, not a
//     user-facing toggle anywhere in apps/web) -- nothing to scan twice.
//   - The `ar` (RTL) locale: follow-up once T-231 lands.
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
    /https:\/\/localhost\/verify-email\?token=[\w-]+/,
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

    const inviteLink = await waitForLink(
      teammateEmail,
      /https:\/\/localhost\/accept-invitation\/[\w.-]+/,
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
        /https:\/\/localhost\/verify-email\?token=[\w-]+/,
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
