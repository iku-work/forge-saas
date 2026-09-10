/**
 * The plan catalogue, fetched once at build time (T-301).
 *
 * The endpoint is `/api/v1/billing/plans` — the plan text says
 * `/api/v1/plans`, but the billing router mounts at `/billing`
 * (`backend/app/modules/billing/router.py`), and it is the router that is
 * right: every other billing path on the marketing side of the fence would
 * have to disagree with it otherwise. It is the one billing route with no
 * auth dependency, which is what makes it fetchable from a build with no
 * credentials.
 *
 * `PUBLIC_API_URL` names the origin to fetch from, alongside the
 * `PUBLIC_SITE_URL` and `PUBLIC_APP_URL` this app already reads. The `PUBLIC_`
 * prefix is Astro's marker for "may be inlined into the browser bundle", and
 * an API origin qualifies — `apps/web` ships the same value to every visitor
 * as `VITE_API_URL`. Nothing secret is fetched here: `/plans` is the one
 * billing route with no auth dependency.
 *
 * When it is unset the build uses the committed catalogue without complaint —
 * that is the normal case for a fresh checkout and for CI, neither of which
 * has a backend running during the frontend build.
 *
 * Failures are never fatal. A pricing page rendered from slightly stale
 * committed data is a far better outcome than a deploy that fails because the
 * API was restarting, so every failure path here ends at `COMMITTED_PLANS` —
 * see `src/content/pricing.ts` for the staleness contract that makes that an
 * acceptable trade.
 */
import {
  COMMITTED_PLANS,
  FREE_PLAN,
  type MarketingPlan,
} from "../content/pricing";

export type PlanSource = "api" | "committed";

export interface PlanCatalogue {
  plans: MarketingPlan[];
  source: PlanSource;
}

/** How long the build will wait for the API before falling back. */
const FETCH_TIMEOUT_MS = 5_000;

/**
 * The catalogue is fetched once per build, not once per page.
 *
 * Every route under `[...locale]` is rendered once per enabled locale, and
 * both `/pricing` and `/` now ask for the plans — so without this a build
 * makes a dozen identical requests, waits on each of them, and prints the
 * fallback warning a dozen times when the API is down. One process builds the
 * whole site, so a module-level promise is the whole of the cache; there is no
 * invalidation to get wrong because the process exits when the build does.
 */
let inFlight: Promise<PlanCatalogue> | undefined;

/** `PlanPublic` as it arrives over the wire, inside the standard envelope. */
interface PlanPublic {
  slug: string;
  name: string;
  price_monthly: number;
  price_yearly: number;
  currency: string;
  features: Record<string, number | boolean>;
}

function isPlanPublic(value: unknown): value is PlanPublic {
  const plan = value as Partial<PlanPublic> | null;
  return (
    typeof plan === "object" &&
    plan !== null &&
    typeof plan.slug === "string" &&
    typeof plan.name === "string" &&
    typeof plan.price_monthly === "number" &&
    typeof plan.price_yearly === "number" &&
    // Checked rather than assumed: a backend older than the currency column
    // would send prices with no way to say what they are denominated in, and
    // rendering them under this site's own guess is the bug the column was
    // added to end. Falling back to the committed catalogue is the honest
    // answer there.
    typeof plan.currency === "string" &&
    typeof plan.features === "object" &&
    plan.features !== null
  );
}

function toMarketingPlan(plan: PlanPublic): MarketingPlan {
  return {
    slug: plan.slug,
    name: plan.name,
    priceMonthly: plan.price_monthly,
    priceYearly: plan.price_yearly,
    currency: plan.currency,
    features: plan.features,
  };
}

/**
 * The catalogue to render, and where it came from.
 *
 * `source` is returned rather than logged only, because the pricing page shows
 * a build stamp when it is `"committed"` — an operator looking at a wrong
 * price should be able to tell "the build could not reach the API" from "the
 * database says this" without reading a build log that has since scrolled
 * away.
 */
export async function loadPlans(): Promise<PlanCatalogue> {
  inFlight ??= fetchPlans();
  return inFlight;
}

async function fetchPlans(): Promise<PlanCatalogue> {
  const origin = import.meta.env.PUBLIC_API_URL;
  if (!origin) {
    return { plans: COMMITTED_PLANS, source: "committed" };
  }

  const url = new URL(
    "api/v1/billing/plans",
    origin.endsWith("/") ? origin : `${origin}/`,
  );

  try {
    const response = await fetch(url, {
      headers: { accept: "application/json" },
      // A build must not hang because the API is accepting connections and
      // then thinking about it.
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    });
    if (!response.ok) {
      throw new Error(`HTTP ${String(response.status)}`);
    }

    const body: unknown = await response.json();
    const data = (body as { data?: unknown } | null)?.data;
    if (!Array.isArray(data) || !data.every(isPlanPublic)) {
      throw new Error("response did not contain a list of plans");
    }
    // An API that is up but has no plans configured is not a reason to
    // advertise the template's committed ones as if they were this product's.
    if (data.length === 0) {
      throw new Error("the API returned an empty plan catalogue");
    }

    // The free tier is never in that response and never can be: it has no
    // Stripe price and no plan row, because an org with no subscription
    // resolves to the backend's in-memory _FREE_PLAN. Advertising only what
    // Stripe sells would drop the Free card off the pricing page the moment
    // the API became reachable -- which is exactly what happened the first
    // time `make sync-plans` ran against a real catalogue.
    //
    // Guarded rather than unconditional: a product that really does sell a
    // "free" plan row keeps whatever the API said about it.
    const fromApi = data.map(toMarketingPlan);
    // Zero is zero in any currency and the card renders the word "Free"
    // rather than a symbol -- but its JSON-LD `Offer` still has to declare
    // one, and a USD offer beside EUR offers is the same "block disagreeing
    // with its page" this file's neighbours exist to avoid. Adopt the
    // catalogue's.
    const free: MarketingPlan = {
      ...FREE_PLAN,
      currency: fromApi[0]?.currency ?? FREE_PLAN.currency,
    };
    const plans = fromApi.some((plan) => plan.slug === FREE_PLAN.slug)
      ? fromApi
      : [free, ...fromApi];

    return { plans, source: "api" };
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    console.warn(
      `[marketing] could not fetch plans from ${url.href} (${reason}) — ` +
        `building /pricing from src/content/pricing.ts instead. ` +
        `Those prices are only as fresh as the last time someone updated that file.`,
    );
    return { plans: COMMITTED_PLANS, source: "committed" };
  }
}
