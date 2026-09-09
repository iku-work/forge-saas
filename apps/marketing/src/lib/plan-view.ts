/**
 * Turning a `MarketingPlan` into the finished strings the pricing island
 * renders (T-301).
 *
 * Separate from the page because T-303 needs the *same* numbers: its pricing
 * JSON-LD has to carry offers that match the rendered prices exactly, and the
 * only way to guarantee that is for both to come from one function rather
 * than from two roundings of the same cents.
 */
import enMarketing from "@forge/i18n/locales/en/marketing.json";

import type { PricingPlanView } from "../components/PricingPlans";
import { FEATURE_ORDER, type MarketingPlan } from "../content/pricing";
import type { Translate } from "./i18n";

/**
 * Plans are USD-denominated at the data model level -- `PlanPublic` has no
 * currency field, the same assumption `apps/web`'s BillingSettingsPage
 * formats under. The locale still governs grouping, the decimal separator and
 * where the symbol goes ("$49.00" vs "49,00 $").
 */
const CURRENCY = "USD";

/** The feature keys the template ships a label for. See `featureLine` for why `en`. */
const FEATURE_COUNT_LABELS: Record<string, string> =
  enMarketing.pricing.feature_labels;
const FEATURE_FLAG_LABELS: Record<string, string> =
  enMarketing.pricing.feature_flags;

/**
 * Exported for tests/structured-data.test.ts, which takes the price out of a
 * rendered `Offer` and asks this to say what the card must therefore read.
 * That is the seam the "offers match the rendered prices" assertion needs: if
 * the JSON-LD ever stops coming from the same cents, the formatted string
 * stops appearing on the page.
 */
export function formatPlanPrice(cents: number, locale: string): string {
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency: CURRENCY,
    // Whole-dollar prices read better without ".00" on a pricing page, but a
    // $4.99 plan must not silently become $5.
    minimumFractionDigits: cents % 100 === 0 ? 0 : 2,
  }).format(cents / 100);
}

/**
 * The discount for paying yearly, or `null` when there is not one -- a plan
 * priced at twelve times its monthly rate gets no badge rather than
 * "Save 0%".
 */
function yearlySavingPercent(plan: MarketingPlan): number | null {
  const yearOfMonths = plan.priceMonthly * 12;
  if (yearOfMonths <= 0 || plan.priceYearly <= 0) {
    return null;
  }
  const percent = Math.round((1 - plan.priceYearly / yearOfMonths) * 100);
  return percent > 0 ? percent : null;
}

/**
 * One feature line.
 *
 * The key space is open: `features` is whatever JSON the product put in the
 * plan row, so an unknown key renders as itself rather than disappearing.
 * Silently dropping it would let a plan advertise less than it sells, and a
 * missing bullet is much harder to notice than an ugly one.
 *
 * Whether a key is known is asked of the *English* namespace rather than of
 * `t()`, for two reasons: `t()` throws on a missing key instead of returning
 * one, so it cannot be used as a probe; and `en` is where the key space is
 * defined -- every other locale falls back to it, so a label that exists in
 * `fr` and not in `en` is a bug in the translation, not a feature this
 * function should render.
 */
function featureLine(
  name: string,
  value: number | boolean,
  t: Translate,
): string | null {
  if (value === false) {
    return null;
  }
  if (typeof value === "boolean") {
    return name in FEATURE_FLAG_LABELS
      ? t(`marketing.pricing.feature_flags.${name}`)
      : name;
  }
  return name in FEATURE_COUNT_LABELS
    ? t(`marketing.pricing.feature_labels.${name}`, { count: value })
    : t("marketing.pricing.feature_unknown", { name, value });
}

function featureLines(plan: MarketingPlan, t: Translate): string[] {
  const known = FEATURE_ORDER.filter((name) => name in plan.features);
  const extra = Object.keys(plan.features).filter(
    (name) => !FEATURE_ORDER.includes(name as (typeof FEATURE_ORDER)[number]),
  );
  return [...known, ...extra].flatMap((name) => {
    const line = featureLine(name, plan.features[name] ?? false, t);
    return line === null ? [] : [line];
  });
}

export function toPricingPlanView(
  plan: MarketingPlan,
  options: { locale: string; t: Translate; ctaHref: string },
): PricingPlanView {
  const { locale, t, ctaHref } = options;
  const free = t("marketing.pricing.free");
  const saving = yearlySavingPercent(plan);

  return {
    slug: plan.slug,
    name: plan.name,
    price: {
      monthly:
        plan.priceMonthly === 0 ? free : formatPlanPrice(plan.priceMonthly, locale),
      yearly:
        plan.priceYearly === 0 ? free : formatPlanPrice(plan.priceYearly, locale),
    },
    cadence: {
      monthly:
        plan.priceMonthly === 0 ? null : t("marketing.pricing.per_month"),
      yearly: plan.priceYearly === 0 ? null : t("marketing.pricing.per_year"),
    },
    saving:
      saving === null
        ? null
        : t("marketing.pricing.yearly_saving", { percent: saving }),
    features: featureLines(plan, t),
    ctaLabel: t("marketing.pricing.cta", { plan: plan.name }),
    ctaHref,
  };
}
