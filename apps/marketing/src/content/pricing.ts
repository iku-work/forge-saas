/**
 * The committed plan catalogue — the fallback the pricing page renders when
 * the build-time fetch does not happen or does not succeed (T-301).
 *
 * ## Why a static site needs this at all
 *
 * Plans live in the `billing` module's database (`backend/app/modules/billing/
 * models.py`), and `output: "static"` means there is no request-time code that
 * could query it. So `src/lib/plans.ts` fetches the catalogue once, at build
 * time, and this file is what the page falls back to. Without it, a backend
 * that happens to be down during a deploy turns "the price list is 20 minutes
 * stale" into "the pricing page has no prices", which is much worse.
 *
 * ## The staleness contract
 *
 * **Prices on this site change on the next deploy, not on the next `UPDATE`.**
 * Whoever changes a plan's price has two jobs, and the second is easy to
 * forget: change it in the database, and rebuild the marketing site. There is
 * no cache to bust and no webhook that will do it — T-306's Lighthouse job and
 * T-307's deploy are the only things that regenerate `dist/`. A product that
 * needs live prices on a marketing page has outgrown `output: "static"`, and
 * that is the revisit trigger ADR-0004 already records.
 *
 * ## Keeping this file honest
 *
 * The values below mirror the seeded catalogue in `backend/app/core/seed.py`,
 * so a fresh checkout builds a pricing page that matches a fresh database.
 * A product that changes its plans should change them here too — it is the
 * page anyone building without a reachable API will see, including every CI
 * run, since CI has no backend during the frontend build.
 *
 * Amounts are USD cents, the same convention `PlanPublic.price_monthly` uses
 * and the same one `apps/web`'s BillingSettingsPage formats — there is no
 * currency field on a plan.
 */

/** A plan as the marketing site needs it: the public catalogue, no Stripe ids. */
export interface MarketingPlan {
  slug: string;
  name: string;
  /** USD cents. */
  priceMonthly: number;
  /** USD cents. `0` on a plan that is not sold yearly. */
  priceYearly: number;
  /**
   * `PlanPublic.features` verbatim: a numeric limit, or a boolean for a
   * feature that is simply included. `dict[str, int]` on the Python side
   * accepts both, because a Python `bool` is an `int`.
   */
  features: Record<string, number | boolean>;
}

export const COMMITTED_PLANS: MarketingPlan[] = [
  {
    slug: "free",
    name: "Free",
    priceMonthly: 0,
    priceYearly: 0,
    features: { seats: 3, storage_gb: 1, api_calls: 1000 },
  },
  {
    slug: "pro",
    name: "Pro",
    priceMonthly: 4900,
    priceYearly: 49000,
    features: {
      seats: 25,
      storage_gb: 50,
      api_calls: 100000,
      advanced_reports: true,
      file_storage: true,
    },
  },
];

/**
 * The order features are listed in on a plan card, so two plans do not show
 * the same limits in a different order. Anything not named here is appended
 * afterwards in the order the API returned it — a new feature key shows up on
 * the page without a code change, just at the bottom.
 */
export const FEATURE_ORDER = [
  "seats",
  "storage_gb",
  "api_calls",
  "advanced_reports",
  "file_storage",
] as const;
