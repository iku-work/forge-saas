/**
 * Which pages this product actually has, in one list (T-301).
 *
 * The header, the footer and — once T-304 lands — the sitemap all read from
 * here, because a module toggle has to remove a page from *every* one of them
 * at once. `/pricing` with `billing` off is the worked example: the page is
 * not built, so a nav link to it would be a 404 in the primary navigation
 * (the dead-link class of bug T-220 already fixed once), and a sitemap entry
 * for it would be a 404 handed to a crawler.
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
