/**
 * Which pages this product actually has, in one list (T-301).
 *
 * The header and the footer both read from here, because a module toggle has
 * to remove a page from both at once. `/pricing` with `billing` off is the
 * worked example: the page is not built, so a nav link to it would be a 404 in
 * the primary navigation -- the dead-link class of bug T-220 already fixed
 * once.
 *
 * The sitemap does *not* read this list, and should not: it lists what was
 * built, which is the stronger answer. `pricing.astro` gates its own
 * `getStaticPaths` on the same toggle, so the page, the link and the sitemap
 * entry go together without any of the three trusting the other two.
 * tests/routes.test.ts asserts all three.
 *
 * Paths here are locale-less; `localePath()` in ./routing puts the locale on
 * the front.
 */
import { modules } from "./product";

export interface NavLink {
  /** Key in the `marketing` i18n namespace, e.g. `marketing.nav.pricing`. */
  labelKey: string;
  /** Locale-less site path, `""` for the landing page. */
  path: string;
}

/** Every page that is a route in its own right, in nav order. */
export function siteLinks(): NavLink[] {
  return [
    { labelKey: "marketing.nav.features", path: "features" },
    // Plans live in the billing module's database. With the module off there
    // is no catalogue, no checkout and nothing to link to.
    ...(modules.billing
      ? [{ labelKey: "marketing.nav.pricing", path: "pricing" }]
      : []),
    { labelKey: "marketing.nav.guides", path: "guides" },
    { labelKey: "marketing.nav.blog", path: "blog" },
  ];
}

/** The legal documents, which are footer-only — linked, never browsed to. */
export function legalLinks(): NavLink[] {
  return [
    { labelKey: "marketing.footer.privacy", path: "privacy" },
    { labelKey: "marketing.footer.terms", path: "terms" },
  ];
}
