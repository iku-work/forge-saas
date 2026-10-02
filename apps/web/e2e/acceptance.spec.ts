// T-220: the Phase 1 acceptance journey ("A new user can sign up -> verify
// email -> invite a teammate -> upgrade to a paid plan. End to end.") as one
// continuous Playwright spec, run on both chromium and webkit per
// playwright.config.ts's projects. Every email in the flow rides through a
// real MailHog inbox (helpers/mailhog.ts) and the paid-plan flip is driven
// by a signed webhook POST (helpers/stripe.ts) rather than a live Stripe
// Checkout redirect -- see stripe.ts's own comment for why. All locators
// below are data-testid only, per this task's own "no text/CSS selectors"
// rule -- a grep step in the e2e-web CI job enforces that this stays true.
import { expect, test } from "@playwright/test";

import { waitForLink } from "./helpers/mailhog";
import { simulateSubscriptionCreated } from "./helpers/stripe";

// Matches backend/.env.e2e's STRIPE_WEBHOOK_SECRET -- both this suite and
// the compose stack it targets read the same fixed dev value, so there's
// nothing to pass at runtime.
const STRIPE_WEBHOOK_SECRET = "whsec_dummy";
// Matches backend/app/core/seed.py's "pro" Plan row.
const PRO_PLAN_STRIPE_PRICE_ID = "price_pro_monthly";

// A fresh id per run keeps every email address unique -- MailHog never
// expires messages, so a fixed address would pick up a stale message from a
// previous run instead of this one.
const runId = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
const ownerEmail = `owner+${runId}@example.com`;
const teammateEmail = `teammate+${runId}@example.com`;
// >= the 12-char minimum in packages/validation's passwordSchema; unique
// per run so it can never collide with the top-1000 breached-password
// denylist the backend additionally checks.
const password = `E2e-Journey-${runId}!Aa1`;

test("signup -> verify -> invite -> upgrade", async ({ page, browser }) => {
  // --- 1. Register -----------------------------------------------------
  await page.goto("/register");
  await page.getByTestId("register-email-input").fill(ownerEmail);
  await page.getByTestId("register-password-input").fill(password);
  await page.getByTestId("register-submit").click();
  await expect(page.getByTestId("register-success")).toBeVisible();

  // --- 2. Verify email via MailHog --------------------------------------
  const verifyLink = await waitForLink(
    ownerEmail,
    /https:\/\/localhost\/[a-z]{2}\/verify-email\?token=[\w-]+/,
  );
  await page.goto(verifyLink);
  await expect(page.getByTestId("verify-email-success")).toBeVisible();
  await page.getByTestId("verify-email-continue-to-login").click();

  // --- Log in as the owner, land on the dashboard -----------------------
  await page.getByTestId("login-email-input").fill(ownerEmail);
  await page.getByTestId("login-password-input").fill(password);
  await page.getByTestId("login-submit").click();
  await expect(page.getByTestId("create-first-org-prompt")).toBeVisible();

  // --- 3. Create org ------------------------------------------------------
  const [createOrgResponse] = await Promise.all([
    page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/v1/orgs") && response.request().method() === "POST",
    ),
    (async () => {
      await page.getByTestId("create-org-name-input").fill(`Acme ${runId}`);
      await page.getByTestId("create-org-submit").click();
    })(),
  ]);
  const orgId: string = (await createOrgResponse.json()).data.id;
  expect(orgId).toBeTruthy();
  await expect(page.getByTestId("app-shell")).toBeVisible();

  // Reports is gated behind the "advanced_reports" plan flag, which the
  // free plan doesn't carry -- asserted absent now so its later appearance
  // (after the upgrade in step 6) is actually evidence of something, not a
  // link that was always there.
  await expect(page.getByTestId("nav-reports")).toHaveCount(0);

  // --- Invite a teammate --------------------------------------------------
  await page.goto("/settings/members");
  await page.getByTestId("members-invite-button").click();
  await page.getByTestId("invite-email-input").fill(teammateEmail);
  await page.getByTestId("invite-role-select").selectOption("member");
  await page.getByTestId("invite-submit").click();
  await expect(page.getByTestId("invite-email-input")).toBeHidden();

  const acceptInvitationLink = await waitForLink(
    teammateEmail,
    /https:\/\/localhost\/[a-z]{2}\/accept-invitation\/[\w.-]+/,
  );

  // --- 4. Second browser context: register, verify, log in, accept ------
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
    // The teammate has no org of their own yet -- same first-run prompt the
    // owner saw in step 3, proving login succeeded before moving on.
    await expect(teammatePage.getByTestId("create-first-org-prompt")).toBeVisible();

    await teammatePage.goto(acceptInvitationLink);
    await teammatePage.getByTestId("accept-invitation-button").click();
    await expect(teammatePage.getByTestId("app-shell")).toBeVisible();
  } finally {
    await teammateContext.close();
  }

  // --- Assert membership visible to both sides ---------------------------
  await page.reload();
  await expect(page.getByTestId("member-row")).toHaveCount(2);

  // --- 5. Upgrade to paid plan (stubbed webhook, not real Checkout) -----
  await simulateSubscriptionCreated({
    webhookSecret: STRIPE_WEBHOOK_SECRET,
    orgId,
    stripePriceId: PRO_PLAN_STRIPE_PRICE_ID,
  });

  // --- 6. Assert plan badge + gated feature unlocked ----------------------
  await page.goto("/settings/billing");
  await expect(page.getByTestId("billing-current-plan")).toHaveText("Pro");
  await page.goto("/");
  await expect(page.getByTestId("nav-reports")).toBeVisible();

  // --- 7. T-233: locale-prefixed URLs, in a real browser ------------------
  // Every goto above was deliberately locale-less -- i.e. the shape of a
  // pre-T-233 bookmark -- and each one landed on a working page, so the
  // backwards-compatibility redirect is already proven by the journey
  // itself. These two assertions pin down what it redirected *to*, and the
  // head tags T-305 will build on. Head tags are read via evaluate() rather
  // than a locator: <link>/<meta> carry no data-testid, and this suite is
  // data-testid-only (see the CI grep).
  await expect(page).toHaveURL(/\/en\/?$/);
  await page.goto("/settings/profile");
  await expect(page).toHaveURL(/\/en\/settings\/profile$/);
  // Polled: LocaleHead writes these in an effect after the URL settles, and
  // a single read can land in between.
  const readHead = () =>
    page.evaluate(() => ({
      canonical: document
        .querySelector('link[rel="canonical"]')
        ?.getAttribute("href"),
      robots: document.querySelector('meta[name="robots"]')?.getAttribute("content"),
      alternates: document.querySelectorAll('link[rel="alternate"]').length,
    }));
  // Behind auth: canonical only, noindex on, no hreflang alternates.
  await expect.poll(readHead).toEqual({
    canonical: "https://localhost/en/settings/profile",
    robots: "noindex, nofollow",
    alternates: 0,
  });
});
