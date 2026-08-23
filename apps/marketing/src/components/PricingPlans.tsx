/**
 * The pricing period switch — the site's one real island (T-301).
 *
 * This is what the island rule allows: monthly-versus-yearly cannot be
 * expressed as markup, because the two price sets have to swap in place
 * without a page load. Everything around it — the header, the footer, the
 * copy, the plan cards themselves — is rendered in `.astro` and costs
 * nothing.
 *
 * It is also the executable form of ADR-0004's constraint 1, inherited from
 * T-300's `island-check.astro`, which this component replaces: `@forge/ui` is
 * React 18 + Radix, every component in it client-only by construction and
 * carrying no `"use client"` directives, and Astro hydrates it exactly as it
 * ships — no directives added to the package, no wrapper layer. The tabs here
 * are Radix's, straight out of `@forge/ui`, and `tests/pricing.test.ts` fails
 * if the island stops appearing in the built HTML.
 *
 * Every string arrives finished. The page translates at build time and passes
 * props; there is no i18next provider, because each island is its own React
 * root and a provider would ship i18next to a static page (see
 * `src/lib/i18n.ts`).
 */
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  buttonVariants,
} from "@forge/ui";

export type BillingPeriod = "monthly" | "yearly";

export interface PricingPlanView {
  slug: string;
  name: string;
  /** Already formatted for the page's locale, or the "Free" label. */
  price: Record<BillingPeriod, string>;
  /** "per month" / "per year", or `null` on a plan that costs nothing. */
  cadence: Record<BillingPeriod, string | null>;
  /** "Save 17%", or `null` when yearly is not a discount. */
  saving: string | null;
  features: string[];
  ctaLabel: string;
  ctaHref: string;
}

export interface PricingPlansProps {
  plans: PricingPlanView[];
  labels: {
    periodLabel: string;
    monthly: string;
    yearly: string;
    includes: string;
  };
}

function PlanGrid({
  plans,
  period,
  includes,
}: {
  plans: PricingPlanView[];
  period: BillingPeriod;
  includes: string;
}) {
  return (
    <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
      {plans.map((plan) => (
        <Card key={plan.slug} className="flex h-full flex-col">
          <CardHeader>
            <CardTitle>{plan.name}</CardTitle>
            <p className="mt-4 flex flex-wrap items-baseline gap-x-2">
              <span className="text-3xl font-semibold tracking-tight">
                {plan.price[period]}
              </span>
              {plan.cadence[period] ? (
                <span className="text-sm text-muted-foreground">
                  {plan.cadence[period]}
                </span>
              ) : null}
            </p>
            {period === "yearly" && plan.saving ? (
              <p className="mt-2 text-sm font-medium text-primary">
                {plan.saving}
              </p>
            ) : null}
          </CardHeader>
          <CardContent className="flex flex-1 flex-col gap-4">
            <div className="flex-1">
              <h3 className="text-sm font-medium">{includes}</h3>
              <ul className="mt-2 space-y-1 text-sm text-muted-foreground">
                {plan.features.map((feature) => (
                  <li key={feature}>{feature}</li>
                ))}
              </ul>
            </div>
            <a
              className={buttonVariants({ className: "w-full" })}
              href={plan.ctaHref}
            >
              {plan.ctaLabel}
            </a>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

export function PricingPlans({ plans, labels }: PricingPlansProps) {
  // Uncontrolled: Radix owns the selected tab, so this island needs no state
  // of its own and both panels are in the server-rendered HTML before it
  // hydrates. A visitor whose JavaScript never arrives still sees the monthly
  // prices rather than an empty box.
  return (
    <Tabs defaultValue="monthly" className="mt-10">
      <TabsList aria-label={labels.periodLabel}>
        <TabsTrigger value="monthly">{labels.monthly}</TabsTrigger>
        <TabsTrigger value="yearly">{labels.yearly}</TabsTrigger>
      </TabsList>
      <TabsContent value="monthly" className="mt-8">
        <PlanGrid plans={plans} period="monthly" includes={labels.includes} />
      </TabsContent>
      <TabsContent value="yearly" className="mt-8">
        <PlanGrid plans={plans} period="yearly" includes={labels.includes} />
      </TabsContent>
    </Tabs>
  );
}
