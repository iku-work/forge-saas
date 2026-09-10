/**
 * The JSON-LD this site emits, built from the data the pages already render
 * (T-303).
 *
 * Nothing here is a second copy of anything. The prices come from the same
 * `MarketingPlan[]` the pricing island renders, the post metadata from the
 * same frontmatter the article shows, the product name from src/config/site.ts
 * like every other mention of it. That is the whole design constraint: a
 * structured-data block that disagrees with the page it is on is worse than no
 * block at all, because a search engine will happily publish the wrong one.
 *
 * `schema-dts` types every builder below. It is a types-only dependency --
 * zero runtime bytes -- and it is what catches "@type": "SoftwareApp" or an
 * `Offer` with `currency` instead of `priceCurrency` at compile time, which is
 * otherwise a mistake you find in Search Console six weeks later.
 *
 * Where these end up: a schema that describes the *page* (the landing page is
 * a SoftwareApplication, a post is a BlogPosting) travels through
 * `BaseLayout`'s `schema` prop into the head, beside <Seo>'s meta. A schema
 * that describes a *block of markup* (the FAQ, the breadcrumb trail) is
 * emitted by the component that renders that markup, from the same array --
 * see src/components/FaqSection.astro. Google requires structured data to
 * reflect visible content, and building both from one array is how that stops
 * being a thing reviewers have to check.
 */
import type {
  AggregateOffer,
  Answer,
  BlogPosting,
  BreadcrumbList,
  FAQPage,
  ListItem,
  Offer,
  Organization,
  Product,
  Question,
  SoftwareApplication,
  Thing,
  WithContext,
} from "schema-dts";

import { site } from "../config/site";
import type { MarketingPlan } from "../content/pricing";
import { absoluteUrl } from "./site-url";

/** Anything this module produces: one top-level node, ready to serialise. */
export type SchemaNode = WithContext<Thing>;

const CONTEXT = "https://schema.org";

/**
 * The currency an `AggregateOffer` quotes, taken from the plans it
 * aggregates rather than assumed. It was a hardcoded "USD" until
 * 2026-09-10, which published a structured-data block contradicting the page
 * it sat on -- the exact failure this file's header calls worse than no block
 * at all, since a search engine will happily index the wrong one.
 *
 * An AggregateOffer carries one currency for the whole range, so a catalogue
 * genuinely mixing currencies would need per-plan `Offer`s instead. Taking
 * the first plan's is right for the single-currency catalogue this supports;
 * `make sync-plans` already refuses a plan whose own two prices disagree.
 */
const DEFAULT_CURRENCY = "USD";

/**
 * UN/CEFACT Recommendation 20 unit codes, which is what schema.org's
 * `QuantitativeValue.unitCode` expects. Without them an `Offer` says "49
 * dollars" and not "49 dollars *per month*", and a price with no period on it
 * is the one number on a pricing page you must not get wrong.
 */
const UNIT_MONTH = "MON";
const UNIT_YEAR = "ANN";

/**
 * JSON-LD as a string safe to drop inside a `<script>` element.
 *
 * A `<script>` is a raw-text element: the HTML parser scans its contents for
 * `</script` and for `<!--`, and nothing else -- no entity references, no
 * attribute parsing. So a payload containing `</script>` (a blog post about
 * this very component, say) would terminate the block early and leave the
 * rest of the JSON in the document as markup. Both of the sequences that can
 * do it start with `<`, so rewriting that one character as its JSON unicode
 * escape closes the hole completely -- and it stays an ordinary JSON string
 * escape, which every parser reads back as `<`.
 *
 * Doing it here, once, is the point: no caller has to remember, and a caller
 * that builds its own JSON-LD by hand is the thing to catch in review.
 */
export function serialiseJsonLd(schema: SchemaNode | SchemaNode[]): string {
  return JSON.stringify(schema).replace(/</g, "\\u003c");
}

/**
 * The site-wide Organization node's identity.
 *
 * Every other node points at this `@id` rather than repeating the
 * organisation inline, so a crawler reading a blog post and a crawler reading
 * the pricing page arrive at the same entity instead of two that share a name.
 */
export function organizationId(astroSite: URL | undefined): string {
  return absoluteUrl("#organization", astroSite);
}

/**
 * The publisher of everything on this site, emitted on every page by
 * `BaseLayout`.
 *
 * `logo` is the favicon endpoint, not `product.config.ts`'s `logoUrl`: that
 * field points at `/logo.svg`, which no app in this repository serves and no
 * file in it provides. Pointing a crawler at a 404 is worse than pointing it
 * at the generated mark, which is drawn from the same `primaryColor` and is
 * the only logo this template actually has.
 */
export function organizationSchema(
  astroSite: URL | undefined,
): WithContext<Organization> {
  return {
    "@context": CONTEXT,
    "@type": "Organization",
    "@id": organizationId(astroSite),
    name: site.name,
    url: absoluteUrl("/", astroSite),
    logo: absoluteUrl("/favicon.svg", astroSite),
    email: site.supportEmail,
  };
}

/**
 * The landing page: the product itself, not the company that makes it.
 *
 * The offers are an `AggregateOffer` -- a range and a count, pointing at
 * /pricing -- rather than the per-plan detail, which belongs beside the prices
 * a reader can check and is what `planProductSchemas` emits there. A
 * SoftwareApplication with no offer at all is not merely thinner: Google's
 * Software App result requires one of `offers`, `aggregateRating` or `review`,
 * and this template has no honest ratings to give.
 *
 * `plans` is empty when `billing` is off, and then there is no `offers` key at
 * all -- a product with no checkout has no prices, and inventing a range for
 * one is exactly the half-applied-toggle failure the module system exists to
 * prevent.
 */
export function softwareApplicationSchema(options: {
  astroSite: URL | undefined;
  url: string;
  description: string;
  locale: string;
  plans: MarketingPlan[];
  /** Absolute URL of the page the offers are bought from. */
  pricingUrl?: string | undefined;
}): WithContext<SoftwareApplication> {
  const { astroSite, url, description, locale, plans, pricingUrl } = options;
  const monthly = plans.map((plan) => plan.priceMonthly);
  const offers: AggregateOffer | undefined =
    monthly.length > 0 && pricingUrl
      ? {
          "@type": "AggregateOffer",
          priceCurrency: (plans[0]?.currency ?? DEFAULT_CURRENCY).toUpperCase(),
          lowPrice: offerPrice(Math.min(...monthly)),
          highPrice: offerPrice(Math.max(...monthly)),
          offerCount: plans.length,
          url: pricingUrl,
        }
      : undefined;

  return {
    "@context": CONTEXT,
    "@type": "SoftwareApplication",
    name: site.name,
    description,
    url,
    applicationCategory: "BusinessApplication",
    operatingSystem: "Web browser",
    inLanguage: locale,
    image: absoluteUrl(site.ogImage, astroSite),
    publisher: { "@id": organizationId(astroSite) },
    ...(offers ? { offers } : {}),
  };
}

/** A plan's price as schema.org wants it: a decimal string, no symbol, no grouping. */
function offerPrice(cents: number): string {
  return (cents / 100).toFixed(2);
}

/**
 * One `Offer` for one billing period.
 *
 * The period is carried by `referenceQuantity` rather than baked into a name,
 * because a name is prose and `1 MON` is a fact. Both offers point at the
 * pricing page: that is where the purchase starts, and an `Offer` whose `url`
 * is a page the price is not on is exactly the mismatch this file exists to
 * avoid.
 */
function planOffer(
  cents: number,
  currency: string,
  unitCode: string,
  url: string,
): Offer {
  const price = offerPrice(cents);
  const priceCurrency = currency.toUpperCase();
  return {
    "@type": "Offer",
    price,
    priceCurrency,
    availability: "https://schema.org/InStock",
    url,
    priceSpecification: {
      "@type": "UnitPriceSpecification",
      price,
      priceCurrency,
      referenceQuantity: {
        "@type": "QuantitativeValue",
        value: 1,
        unitCode,
      },
    },
  };
}

/**
 * One `Product` per plan, each with a monthly and a yearly `Offer`.
 *
 * `plans` is whatever the build fetched (or the committed fallback) -- the
 * *same* array src/lib/plan-view.ts turns into the rendered cards. A price can
 * therefore only be wrong here if it is equally wrong on the page, which is
 * the property tests/structured-data.test.ts asserts over both at once.
 */
export function planProductSchemas(
  plans: MarketingPlan[],
  options: { astroSite: URL | undefined; url: string },
): WithContext<Product>[] {
  const { astroSite, url } = options;
  return plans.map((plan) => ({
    "@context": CONTEXT,
    "@type": "Product",
    "@id": `${url}#plan-${plan.slug}`,
    name: plan.name,
    brand: { "@id": organizationId(astroSite) },
    offers: [
      planOffer(plan.priceMonthly, plan.currency, UNIT_MONTH, url),
      planOffer(plan.priceYearly, plan.currency, UNIT_YEAR, url),
    ],
  }));
}

/** A question and its answer, as both the markup and the JSON-LD need them. */
export interface FaqItem {
  question: string;
  answer: string;
}

export function faqPageSchema(items: FaqItem[]): WithContext<FAQPage> {
  const mainEntity: Question[] = items.map((item) => ({
    "@type": "Question",
    name: item.question,
    acceptedAnswer: { "@type": "Answer", text: item.answer } satisfies Answer,
  }));
  return { "@context": CONTEXT, "@type": "FAQPage", mainEntity };
}

/**
 * A blog post.
 *
 * `dateModified` falls back to `datePublished` rather than being omitted: a
 * post with no modification date is not a post of unknown freshness, it is a
 * post that has not been touched since it was published, and saying so is
 * more useful than saying nothing.
 */
export function blogPostingSchema(options: {
  astroSite: URL | undefined;
  url: string;
  title: string;
  description: string;
  author: string;
  publishedAt: Date;
  updatedAt?: Date | undefined;
  image?: string | undefined;
  tags: string[];
  locale: string;
}): WithContext<BlogPosting> {
  const {
    astroSite,
    url,
    title,
    description,
    author,
    publishedAt,
    updatedAt,
    image,
    tags,
    locale,
  } = options;
  return {
    "@context": CONTEXT,
    "@type": "BlogPosting",
    headline: title,
    description,
    datePublished: publishedAt.toISOString(),
    dateModified: (updatedAt ?? publishedAt).toISOString(),
    author: { "@type": "Person", name: author },
    image: absoluteUrl(image ?? site.ogImage, astroSite),
    inLanguage: locale,
    // The URL this content is canonically at, which is what stops the same
    // post in six locales from reading as six copies of one page.
    mainEntityOfPage: { "@type": "WebPage", "@id": url },
    publisher: { "@id": organizationId(astroSite) },
    ...(tags.length > 0 ? { keywords: tags.join(", ") } : {}),
  };
}

/** One step of a breadcrumb trail. The last one has no `url` -- it is this page. */
export interface Crumb {
  name: string;
  url?: string | undefined;
}

/**
 * A breadcrumb trail.
 *
 * The final crumb deliberately carries no `item`: it is the page being
 * looked at, and schema.org's guidance is to leave the last element's URL out
 * rather than have it point at itself.
 */
export function breadcrumbListSchema(
  crumbs: Crumb[],
): WithContext<BreadcrumbList> {
  const itemListElement: ListItem[] = crumbs.map((crumb, index) => ({
    "@type": "ListItem",
    position: index + 1,
    name: crumb.name,
    ...(crumb.url ? { item: crumb.url } : {}),
  }));
  return { "@context": CONTEXT, "@type": "BreadcrumbList", itemListElement };
}
