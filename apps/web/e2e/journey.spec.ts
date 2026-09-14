// The billing lifecycle a customer actually lives through, plus the session
// guard around it, driven end to end against a real stack.
//
// Why this exists: between 2026-09-12 and 2026-09-14 six billing defects
// reached production, and every one of them was found by a person clicking
// rather than by this suite. Each act below is one of them. A unit test
// could not have caught them -- they live in the seam between Stripe's
// payloads, the webhook handlers, the cached entitlement and the page that
// renders it.
//
// Signs in as the seeded superadmin (backend/app/core/seed.py) rather than
// registering, which is what lets this run on the plain dev stack:
// acceptance.spec.ts needs Mailhog for its verification email and therefore
// the .env.e2e swap, and none of that is needed to exercise billing.
//
// Stripe is driven by posting real HMAC-signed events at the webhook
// endpoint (helpers/stripe.ts). Deterministic, offline, and it tests our
// handlers exactly. What it deliberately does not do is drive Stripe's own
// Checkout page: that is Stripe's UI, it breaks when they restyle it, and a
// declined card never returns to us anyway -- Stripe keeps the customer on
// its own page to retry.
import { expect, test } from "@playwright/test";

import {
  simulateInvoiceEvent,
  simulateSubscriptionEvent,
} from "./helpers/stripe";

// Matches backend/.env's STRIPE_WEBHOOK_SECRET, which .env.e2e also shares,
// so this spec runs unchanged under either.
const STRIPE_WEBHOOK_SECRET = "whsec_dummy";
// Seeded by app.core.seed as the Pro plan's monthly price.
const PRO_PRICE_ID = "price_pro_monthly";

const EMAIL = "admin@example.com";
const PASSWORD = "DevPassword123!";

async function signIn(page: import("@playwright/test").Page): Promise<void> {
  await page.goto("/en/login");
  await page.getByTestId("login-email-input").fill(EMAIL);
  await page.getByTestId("login-password-input").fill(PASSWORD);
  await page.getByTestId("login-submit").click();
  await expect(page.getByTestId("app-shell")).toBeVisible();
}

/**
 * A brand-new org for this run, and the id of it.
 *
 * Not the seeded org: this journey subscribes, fails a payment and cancels,
 * so running it twice against one org would start the second run already on
 * Pro -- and a run that fails midway leaves the org subscribed, poisoning
 * every run after it. That happened while writing this, which is the only
 * reason it is written this way.
 *
 * Created through the switcher's own dialog rather than the API, because the
 * access token lives in memory and `page.request` has no way to reach it.
 * The id comes off the POST the form makes, the same trick
 * acceptance.spec.ts uses.
 */
async function createFreshOrg(page: import("@playwright/test").Page): Promise<string> {
  // Back inside the shell first: the previous step deliberately ends on
  // /magic-link, which is a public page with no switcher on it.
  await page.goto("/en");
  await expect(page.getByTestId("app-shell")).toBeVisible();

  const created = page.waitForResponse(
    (r) => r.url().endsWith("/api/v1/orgs") && r.request().method() === "POST",
  );
  await page.getByTestId("org-switcher-trigger").click();
  await page.getByTestId("org-switcher-create").click();
  await page
    .getByTestId("create-org-name-input")
    .fill(`Journey ${String(Date.now())}`);
  await page.getByTestId("create-org-submit").click();

  const body = (await (await created).json()) as { data: { id: string } | null };
  const orgId = body.data?.id;
  expect(orgId, "creating an org should return its id").toBeTruthy();
  await expect(page.getByTestId("app-shell")).toBeVisible();
  return orgId as string;
}

async function planBadge(page: import("@playwright/test").Page): Promise<string> {
  await page.goto("/en/settings/billing");
  const badge = page.getByTestId("billing-current-plan");
  await expect(badge).toBeVisible();
  return ((await badge.textContent()) ?? "").trim();
}

test.describe("the billing lifecycle", () => {
  test.use({ reducedMotion: "reduce" });

  // One test, not several, and one login. The auth endpoints are rate
  // limited to 5/minute per IP (playwright.config.ts says as much about why
  // its projects run serially), and a spec that signs in per test spends
  // that budget on itself: running this file twice in a minute started
  // failing with no app shell, which is a 429 wearing a disguise.
  test("session guard, then subscribe, fail, recover, cancel", async ({ page }) => {
    await signIn(page);

    // Act 1. Deployed 2026-09-14: opening the app in a second tab and
    // clicking "sign in" showed a login form to somebody already
    // authenticated, who would then rotate their own refresh cookie by
    // obeying it.
    await test.step("a signed-in visitor is never shown a login form", async () => {
      for (const path of ["/en/login", "/en/forgot-password"]) {
        await page.goto(path);
        await expect(page.getByTestId("app-shell")).toBeVisible();
        expect(new URL(page.url()).pathname, `${path} should redirect`).toBe("/en");
      }

    });

    const orgId = await createFreshOrg(page);
    const subscriptionId = `sub_journey_${String(Date.now())}`;
    const stripe = { webhookSecret: STRIPE_WEBHOOK_SECRET, orgId };

    expect(await planBadge(page), "starts on the free tier").toContain("Free");

    // Act 2. The Basil shape: the billing period lives on the item, not on
    // the subscription. Reading it off the subscription raised KeyError in
    // production and left a paying customer on Free.
    await test.step("a new subscription flips the plan to Pro", async () => {
      await simulateSubscriptionEvent("customer.subscription.created", {
        ...stripe,
        stripePriceId: PRO_PRICE_ID,
        subscriptionId,
      });
      expect(await planBadge(page)).toContain("Pro");
    });

    // Act 3. Two live subscriptions on one org happened for real, and cost
    // two charges. There are two layers between a customer and that, and
    // both are worth pinning because either alone is one bug from failing.
    await test.step("a second subscription is refused, not sold", async () => {
      await page.goto("/en/settings/billing");

      // Layer one, in the page: the plan they already have is not buyable.
      const proButton = page.getByTestId("billing-upgrade-pro");
      await expect(proButton).toBeDisabled();

      // Layer two, in the server. Driven by clicking rather than by
      // page.request, which carries no Authorization header -- the access
      // token lives in memory in the app, so an out-of-band call is simply
      // a 401 and proves nothing. Any plan will do: the guard is about the
      // subscription the org already has, not the one it is asking for.
      const refused = page.waitForResponse(
        (r) =>
          r.url().includes("/api/v1/billing/checkout-session") &&
          r.request().method() === "POST",
      );
      await page.getByTestId("billing-upgrade-free").click();
      const response = await refused;

      // Exactly 409, not merely "not 200". `not.toBe(200)` passed with the
      // guard deleted, because the unguarded path then failed on the dev
      // stack's dummy Stripe key and returned 5xx -- a test that could not
      // tell the fix from its absence. 409 also pins that the refusal does
      // not depend on Stripe answering: building its portal URL is best
      // effort precisely so this stays a 409 when Stripe is unreachable,
      // which on this stack it always is.
      expect(
        response.status(),
        "an org with a live subscription must not be able to buy another",
      ).toBe(409);
      const body = (await response.json()) as { error?: { message_key?: string } };
      expect(body.error?.message_key).toBe("errors.subscription_already_active");
    });

    // Act 4. Both invoice handlers read `invoice.subscription`, which Basil
    // removed, so they returned early and dunning never started for anyone.
    await test.step("a failed payment moves the subscription to past_due", async () => {
      await simulateInvoiceEvent("invoice.payment_failed", {
        ...stripe,
        subscriptionId,
      });
      await page.goto("/en/settings/billing");
      await expect(page.getByTestId("billing-subscription-status")).toContainText(
        "past_due",
      );
      // Still Pro: past_due is inside the grace period, and telling a
      // customer they are on Free while you chase their card is its own lie.
      expect(await planBadge(page)).toContain("Pro");
    });

    // Act 6, taken before the cancel so there is still something to protect:
    // Stripe redelivers, and a retried failure must not undo a later success.
    await test.step("a stale redelivery cannot undo a newer event", async () => {
      const recovered = Math.floor(Date.now() / 1000);
      await simulateInvoiceEvent("invoice.payment_succeeded", {
        ...stripe,
        subscriptionId,
        createdAt: recovered,
      });
      await simulateInvoiceEvent("invoice.payment_failed", {
        ...stripe,
        subscriptionId,
        createdAt: recovered - 600,
      });
      await page.goto("/en/settings/billing");
      await expect(page.getByTestId("billing-subscription-status")).toContainText(
        "active",
      );
    });

    // Act 5. A cancelled row kept pointing at the plan it used to grant, so
    // the page said "Pro" beside a status of "canceled" -- and disabled
    // Pro's button as "Current plan", stranding anyone who wanted to
    // resubscribe.
    await test.step("cancelling returns the org to Free and re-offers Pro", async () => {
      await simulateSubscriptionEvent("customer.subscription.updated", {
        ...stripe,
        stripePriceId: PRO_PRICE_ID,
        subscriptionId,
        status: "canceled",
      });
      expect(await planBadge(page)).toContain("Free");
      await expect(page.getByTestId("billing-upgrade-pro")).toBeEnabled();
    });

    // Act 5b. Coming back from an abandoned Checkout used to be silent,
    // which reads as "did something break?".
    await test.step("an abandoned checkout says so", async () => {
      await page.goto("/en/settings/billing?checkout=canceled");
      await expect(page.getByTestId("billing-checkout-canceled")).toBeVisible();
    });

    // Last on purpose. The redirect guard has an exception for a token,
    // since an emailed link may be for a different account than the one
    // signed in -- but landing here fires a consume that 401s on a bogus
    // token, and the api-client's 401 handling then runs concurrently with
    // whatever the test does next. Nothing follows it, so nothing races it.
    // (The session does survive: probed separately, and refresh/me both
    // still answer 200 afterwards.)
    await test.step("an emailed token is still honoured while signed in", async () => {
      await page.goto("/en/magic-link?token=deliberately-invalid");
      expect(new URL(page.url()).pathname).toBe("/en/magic-link");
    });
  });
});
