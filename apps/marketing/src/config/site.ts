/**
 * The one place the brand is written down for this app (T-302).
 *
 * Everything here is *derived* from product.config.ts -- the product name,
 * the brand colour, the support address -- so a fork edits that file and
 * nothing else. `src/lib/product.ts` is the reader; this is the marketing
 * site's view of what it read, with the meta-block defaults that only exist
 * here (the Open Graph image, its dimensions, the title suffix, the
 * territory Facebook wants on a locale).
 *
 * The split is worth stating: `product.ts` answers "what is configured"
 * (modules, locales, the app's URL), `site.ts` answers "how does this
 * product present itself". Pages import whichever they actually mean, and
 * neither one of them repeats a literal from product.config.ts.
 *
 * The site's *origin* is deliberately not here. It is `site` in
 * astro.config.mjs, read once from PUBLIC_SITE_URL, and reaches a page as
 * `Astro.site` -- a second copy of that fallback in TypeScript is exactly the
 * drift this file exists to prevent.
 */
import { product } from "../lib/product";

/** Separator between a page's own title and the product name. */
const TITLE_SEPARATOR = " — ";

/**
 * Open Graph's canonical card size. Facebook, LinkedIn and X all render
 * 1.91:1, and every one of them prefers to be told the dimensions in the
 * markup rather than fetch the image to find out.
 */
export const OG_IMAGE_WIDTH = 1200;
export const OG_IMAGE_HEIGHT = 630;

export const site = {
  name: product.name,
  brandColor: product.primaryColor,
  supportEmail: product.supportEmail,
  /**
   * The committed default card, drawn from `primaryColor` by
   * `scripts/make-og-image.mjs`. Per-entry overrides come from the `ogImage`
   * field in src/content.config.ts; generating one per page at build time is
   * an explicit non-goal (it buys a rendering dependency and a CI browser).
   */
  ogImage: "/og-default.png",
} as const;

/**
 * A page's `<title>`: its own title, then the product's.
 *
 * The landing page's title *is* the product name, and "ForgeSaaS — ForgeSaaS"
 * is the classic way that goes wrong, so the suffix is skipped when it would
 * repeat.
 */
export function pageTitle(title: string): string {
  const trimmed = title.trim();
  return trimmed === site.name ? trimmed : `${trimmed}${TITLE_SEPARATOR}${site.name}`;
}

/**
 * `og:locale` is `language_TERRITORY`, not a bare language code -- Facebook's
 * list is territory-qualified and a bare `en` is ignored.
 *
 * The territory is the language code uppercased for every locale this
 * template ships (`fi_FI`, `de_DE`, `es_ES`, `fr_FR`, `ar_AR` are all real
 * entries in that list), so only the exceptions are written out. English is
 * the exception: there is no `en_EN`.
 */
const OG_TERRITORY: Readonly<Record<string, string>> = { en: "US" };

export function ogLocale(locale: string): string {
  return `${locale}_${OG_TERRITORY[locale] ?? locale.toUpperCase()}`;
}
