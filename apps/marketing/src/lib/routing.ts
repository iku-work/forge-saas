/**
 * Where the locale lives in a marketing URL, and nowhere else (T-301).
 *
 * Every page under `src/pages/[...locale]/` builds once per enabled locale,
 * and every one of them gets its paths from `localeStaticPaths()` and its
 * links from `localePath()`. That concentration is the point:
 *
 * **T-305 owns the prefix policy, and this file plus `i18n.routing` in
 * astro.config.mjs are the whole of it.** Today the default locale is
 * unprefixed -- `/pricing` is English, `/fr/pricing` is French -- which is
 * what T-300 left configured and what tests/build.test.ts already pins by
 * asserting the landing copy at `dist/index.html`.
 *
 * That is *not* what apps/web does. T-233 prefixes every URL including the
 * default's (`/en/settings`), and redirects locale-less ones. T-305's job is
 * to make the two identical, which means flipping `DEFAULT_LOCALE_IS_PREFIXED`
 * below, flipping `prefixDefaultLocale` alongside it, adding the `/` -> `/en/`
 * redirect a static site needs, and moving T-300's landing-page assertions
 * onto `/en/`. Do not settle it here by guessing -- read T-233 first.
 */
import { defaultLocale, enabledLocales } from "./product";

/**
 * The single switch. `false` matches astro.config.mjs's
 * `routing.prefixDefaultLocale: false`; the two are one decision and must
 * move together, which tests/routes.test.ts asserts.
 */
const DEFAULT_LOCALE_IS_PREFIXED = false;

export { defaultLocale, enabledLocales };

/**
 * The value of the `[...locale]` rest parameter for a locale: a segment, or
 * `undefined` for "no segment at all".
 *
 * A rest parameter is used rather than `[locale]` precisely so `undefined` is
 * expressible -- it is what lets one page file serve both `/pricing` and
 * `/fr/pricing`, instead of the usual Astro shape of a duplicated page tree
 * (`src/pages/pricing.astro` plus `src/pages/[locale]/pricing.astro`) where
 * the two copies drift.
 */
export function localeSegment(locale: string): string | undefined {
  return !DEFAULT_LOCALE_IS_PREFIXED && locale === defaultLocale
    ? undefined
    : locale;
}

/**
 * An absolute site path under `locale`: `localePath("fr", "blog")` is
 * `/fr/blog/`, `localePath("en", "blog")` is `/blog/`.
 *
 * Trailing slash on purpose. Astro's default `trailingSlash: "ignore"` writes
 * `blog/index.html`, which a static server serves at `/blog/`; linking to
 * `/blog` makes Caddy issue a redirect on every internal click, and T-306's
 * Lighthouse run counts those.
 */
export function localePath(locale: string, path = ""): string {
  const segments = [localeSegment(locale), ...path.split("/")].filter(
    (segment): segment is string => Boolean(segment),
  );
  return segments.length === 0 ? "/" : `/${segments.join("/")}/`;
}

/** One `getStaticPaths()` entry per enabled locale, for a page with no other params. */
export function localeStaticPaths(): {
  params: { locale: string | undefined };
  props: { locale: string };
}[] {
  return enabledLocales.map((locale) => ({
    params: { locale: localeSegment(locale) },
    props: { locale },
  }));
}

/**
 * The locale an entry belongs to, and its slug within that locale, from a
 * content-collection id like `en/hello-world` or `en/deploying/caddy`.
 *
 * Returns `undefined` for an entry whose first path segment is not an enabled
 * locale, so a file dropped into `src/content/blog/` without a locale
 * directory is skipped rather than published at a URL nobody meant.
 */
export function splitEntryId(
  id: string,
): { locale: string; slug: string } | undefined {
  const [locale, ...rest] = id.split("/");
  if (!locale || !enabledLocales.includes(locale) || rest.length === 0) {
    return undefined;
  }
  return { locale, slug: rest.join("/") };
}
